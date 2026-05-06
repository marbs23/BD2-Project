
import struct, os, csv, json
from dataclasses import dataclass
from typing import Optional, List

RECORD_FORMAT      = "i100s40sifi"
RECORD_SIZE        = struct.calcsize(RECORD_FORMAT)   # 156 bytes
PAGE_SIZE          = 4096
BUCKET_HEADER_FMT  = "ii"                             # local_depth, n_records
BUCKET_HEADER_SIZE = struct.calcsize(BUCKET_HEADER_FMT)  # 8 bytes
BUCKET_CAPACITY    = (PAGE_SIZE - BUCKET_HEADER_SIZE) // RECORD_SIZE  # 26


@dataclass
class Record:
    id:     int
    title:  str
    author: str
    pages:  int
    rating: float
    year:   int


def _pack(r: Record) -> bytes:
    return struct.pack(
        RECORD_FORMAT,
        r.id,
        r.title.encode("utf-8")[:100].ljust(100, b"\x00"),
        r.author.encode("utf-8")[:40 ].ljust(40,  b"\x00"),
        r.pages, r.rating, r.year,
    )

def _unpack(data: bytes) -> Record:
    rid, title, author, pages, rating, year = struct.unpack(RECORD_FORMAT, data)
    return Record(
        id=rid,
        title =title.decode("utf-8", errors="replace").rstrip("\x00"),
        author=author.decode("utf-8", errors="replace").rstrip("\x00"),
        pages=pages, rating=round(rating, 2), year=year,
    )

#Funcion de direccionamiento indexado
def bit_valor(key: int, depth: int) -> int:
    # profundidad total de bits a considerar para el hashing (global_depth o local_depth)
    return key % (2** depth)


class ExtendibleHashFile:

    def __init__(self, data_path: str, index_path: str):
        self.data_path  = data_path # ruta al archivo de datos (binario)
        self.index_path = index_path
        self.disk_reads = self.disk_writes = 0
        self.auto_save = True  # Para desactivar durante bulk loads

        if os.path.exists(data_path) and os.path.exists(index_path):
            self._f = open(data_path, "r+b")
            self._load_dir()
        else:
            self._f = open(data_path, "w+b")# crear el archivo de datos si no existe
            self.global_depth = 1
            self.directory    = [0, PAGE_SIZE]   # inicialmente 2 buckets: bucket 0 @ offset 0, bucket 1 @ offset PAGE_SIZE
            self.create_bucket(1)                # bucket 0  @ offset 0
            self.create_bucket(1)                # bucket 1  @ offset PAGE_SIZE
            self._save_dir()

    def _save_dir(self):
        with open(self.index_path, "w") as f:
            json.dump({"global_depth": self.global_depth,
                       "directory":    self.directory}, f)

    def _load_dir(self):
        with open(self.index_path) as f:
            d = json.load(f)
        self.global_depth = d["global_depth"]
        self.directory    = d["directory"]

    def create_bucket(self, local_depth: int) -> int:
        self._f.seek(0, os.SEEK_END) #ir al final del archivo para escribir el nuevo bucket
        off = self._f.tell()#posición actual (offset) para el nuevo bucket
        #escribir el encabezado del bucket (local_depth, n_records=0) seguido de bytes nulos para llenar la página
        self._f.write(struct.pack(BUCKET_HEADER_FMT, local_depth, 0)
                      + b"\x00" * (PAGE_SIZE - BUCKET_HEADER_SIZE))
        self._f.flush() #asegurar que los datos se escriban en disco
        self.disk_writes += 1
        return off #retornar el offset del nuevo bucket creado

    def _read_bucket(self, off: int):
        self._f.seek(off) #ir al offset del bucket a leer
        page = self._f.read(PAGE_SIZE) #leer toda la página del bucket (incluyendo el encabezado y los registros)
        self.disk_reads += 1
        ld, n = struct.unpack(BUCKET_HEADER_FMT, page[:BUCKET_HEADER_SIZE]) #``ld es local depth, n es el número de registros
        recs  = []
        #reconstruir los registros a partir de los bytes leídos, usando el formato definido y el número de registros almacenados en el encabezado
        for i in range(n):
            s = BUCKET_HEADER_SIZE + i * RECORD_SIZE
            recs.append(_unpack(page[s: s + RECORD_SIZE]))
        return ld, recs
    

    # ld es local depth
    def _write_bucket(self, off: int, ld: int, records: List[Record]):
        #convertir la lista de registros a bytes usando el formato definido, y escribir el encabezado actualizado (local_depth y número de registros) seguido de los bytes de los registros, rellenando con bytes nulos hasta completar la página
        body = b"".join(_pack(r) for r in records)
        #rellenar hasta completar la pagina
        body += b"\x00" * (PAGE_SIZE - BUCKET_HEADER_SIZE - len(body))
        self._f.seek(off)
        #escribir el encabezado del bucket (local_depth, n_records) seguido de los bytes de los registros y el relleno
        self._f.write(struct.pack(BUCKET_HEADER_FMT, ld, len(records)) + body)
        self._f.flush()
        self.disk_writes += 1

    def _split(self, off: int, records: List[Record], old_ld: int):

        new_ld = old_ld + 1 #ahora el nuevo local depth es uno más que el anterior

        # si el local depth del bucket que se va a dividir es igual al global depth, entonces se necesita aumentar el global depth y duplicar la directory para acomodar los nuevos buckets
        if old_ld == self.global_depth:
            #ahora necesito mirar mas bits para decidir a qué bucket va cada registro, por lo que el global depth aumenta en 1
            self.global_depth += 1 #aumentar el global depth
            self.directory = self.directory * 2 #duplicar la directory (cada entrada se repite dos veces) para acomodar los nuevos buckets
            if self.auto_save:
                self._save_dir() #guardar la directory actualizada en el archivo de índice

        # crear un nuevo bucket con el nuevo local depth y obtener su offset
        new_off = self.create_bucket(new_ld)

        # Redistribuir los registros entre el bucket original y el nuevo bucket según el nuevo local depth. El bit relevante para decidir a qué bucket va cada registro es el bit correspondiente al nuevo local depth (new_ld - 1)
        bit  = new_ld - 1 # voy a mirar el bit correspondiente al nuevo local depth para decidir a qué bucket va cada registro
        keep = []
        move = []
        for r in records:
            index = bit_valor(r.id, new_ld) #calcular el índice del registro usando el nuevo local depth para determinar a qué bucket debería ir
            # BUG CORREGIDO: la variable se llamaba 'bit_valor' igual que la función,
            # sobreescribiéndola en el scope local. Renombrada a 'bit_val'.
            bit_val = (index >> bit) & 1 #obtener el valor del bit relevante para decidir a qué bucket va el registro
            if bit_val == 0:
                keep.append(r) #si el bit es 0, el registro se queda en el bucket original
            else:
                move.append(r) #si el bit es 1, el registro se mueve al nuevo bucket

        self._write_bucket(off,     new_ld, keep)
        self._write_bucket(new_off, new_ld, move)

        # Re-point directory entries
        for i in range(len(self.directory)):

            # solo revisar entradas que apuntan al bucket viejo
            if self.directory[i] == off:

                # BUG CORREGIDO: antes se usaba bit_valor(i, new_ld) que aplica la
                # función hash sobre el índice i como si fuera una clave. Pero i YA ES
                # el prefijo de bits del directorio, por lo tanto solo hay que extraer
                # directamente el bit relevante de i sin hashear.
                bit_val_dir = (i >> bit) & 1      # ver el bit relevante del índice de directorio

                if bit_val_dir == 1:
                    self.directory[i] = new_off      # ahora apunta al bucket nuevo
                # si bit_val_dir == 0, la entrada sigue apuntando a off (bucket original), no se toca
        if self.auto_save:
            self._save_dir() #guardar la directory actualizada en el archivo de índice

    def insert(self, rec: Record):
        # BUG CORREGIDO: se reemplazó el if/else simple por un bucle while.
        # Antes: se llamaba _split una sola vez y se retornaba sin insertar rec.
        # Ahora: _split redistribuye todos los registros de la lista (incluyendo rec,
        # que ya fue agregado antes del split). El bucle reintenta para confirmar
        # que rec quedó en el bucket correcto post-split, y maneja el caso raro
        # en que un bucket sigue lleno tras el split (claves con colisión total de bits).
        max_retries = 100  # Límite para evitar loop infinito
        retries = 0
        while retries < max_retries:
            retries += 1
            di  = bit_valor(rec.id, self.global_depth) #tomar D bits -> índice de la directory
            off = self.directory[di] # flecha: indice al bucket correspondiente
            ld, records = self._read_bucket(off)# leer el bucket para obtener su local depth y los registros actuales

            if any(r.id == rec.id for r in records):
                return                            # duplicate → skip

            records.append(rec)

            if len(records) <= BUCKET_CAPACITY:
                self._write_bucket(off, ld, records)
                return  # insert exitoso, salir del bucle
            else:
                # Never write an overfull page; split directly from memory
                self._split(off, records, ld)
                # continuar el bucle: post-split, verificar en qué bucket quedó rec
        
        # Si llegamos aquí, hubo demasiados reintentos
        print(f"Advertencia: No se pudo insertar registro {rec.id} después de {max_retries} reintentos")


    def search(self, key: int) -> Optional[Record]:
        off = self.directory[bit_valor(key, self.global_depth)] #obtener el offset del bucket correspondiente al key usando el índice de la directory
        _, records = self._read_bucket(off)
        for r in records:
            if r.id == key:
                return r
        return None
    
    def remove(self, key: int) -> bool:
        di  = bit_valor(key, self.global_depth)#obtener el índice de la directory para el key
        off = self.directory[di]#obtener el offset del bucket correspondiente al key usando el índice de la directory
        ld, records = self._read_bucket(off)#leer el bucket para obtener su local depth y los registros actuales
        new_recs = [r for r in records if r.id != key]#crear una nueva lista de registros que excluya el registro con el key a eliminar
        if len(new_recs) == len(records):
            return False
        self._write_bucket(off, ld, new_recs)#escribir el bucket actualizado con la nueva lista de registros (sin el registro eliminado)
        return True

    def reset_counters(self):
        self.disk_reads = self.disk_writes = 0

    def close(self):
        self._f.close()

   # ── CSV bulk load ──────────────────────────────────────────────────────────
    @classmethod
    def from_csv(cls,
                 data_path: str,
                 index_path: str,
                 csv_path: str,
                 delimiter: str = ",") -> "ExtendibleHashFile":
        """
        Build index from CSV. Expected columns: id, title, author, pages, rating, year
        Change the row["..."] keys below if your CSV uses different column names.
        """
        eh = cls(data_path, index_path)
        eh.auto_save = False  # Desactivar guardado automático durante carga
        
        # Detectar codificación automáticamente
        try:
            encoding = "utf-8"
            with open(csv_path, newline="", encoding=encoding) as f:
                f.readline()  # Leer una línea para detectar si funciona
        except UnicodeDecodeError:
            encoding = "utf-16"
        
        with open(csv_path, newline="", encoding=encoding) as f:
            for i, row in enumerate(csv.DictReader(f, delimiter=delimiter)):
                try:
                    eh.insert(Record(
                        id    = int(row["book_key"]),              # ← cambio
                        title = row["title"],
                        author= row["author"],
                        pages = int(row["pages"]) if row["pages"] else 0,  # ← valor por defecto 0
                        rating= float(row["average_rating"]),      # ← cambio
                        year = int(row["published_date"][:4]) if row["published_date"] else 0    # ← extraer año
                    ))
                    if (i + 1) % 100 == 0:
                        print(f"  Procesados {i + 1} registros...")
                except Exception as e:
                    if i < 10:  # Solo mostrar primeros 10 errores
                        print(f"Error en fila {i}: {e}")
        
        eh.auto_save = True  # Reactivar
        eh._save_dir()  # Guardar UNA SOLA VEZ al final
        return eh


# ── demo ───────────────────────────────────────────────────────────────────────
def main():
    DATA  = "books_eh.bin"
    INDEX = "books_eh.json"
    CSV   = "books.csv"      # ← usar CSV pequeño para pruebas

    for p in (DATA, INDEX):
        if os.path.exists(p): 
            try:
                os.remove(p)
            except:
                pass

    print("Cargando CSV …")
    eh = ExtendibleHashFile.from_csv(DATA, INDEX, CSV)
    print(f"  global_depth   = {eh.global_depth}")
    print(f"  directory size = {len(eh.directory)}")
    print(f"  disk reads  (carga) = {eh.disk_reads}")
    print(f"  disk writes (carga) = {eh.disk_writes}")

    eh.reset_counters()
    r = eh.search(1)
    print(f"\nsearch(1): {r}")
    print(f"  accesos a disco: {eh.disk_reads + eh.disk_writes}")

    eh.reset_counters()
    eh.insert(Record(999999, "Test Book", "Test Author", 300, 4.5, 2024))
    print(f"\ninsert(999999): accesos = {eh.disk_reads + eh.disk_writes}")

    eh.reset_counters()
    print(f"\nremove(999999): {eh.remove(999999)}")
    print(f"  accesos: {eh.disk_reads + eh.disk_writes}")
    print(f"  tras borrar: {eh.search(999999)}")

    eh.close()


if __name__ == "__main__":
    main()