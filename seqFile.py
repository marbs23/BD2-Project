from typing import Optional, List, Tuple
from dataclasses import dataclass
import struct
import csv
import os

# por ahora los registros están modelados en su versión plantilla
# al hacer el chequeo final agregaré la forma del csv elegido
# tmb las funciones piden un bool : is_aux | evaluaré si esto es lo correcto o lo corregiré

# definimos un registro
# "i" = int (4 bytes) | "20s" = string de 20 bytes
# "i" = sig_archivo (4 bytes) | "i" = sig_pos (4 bytes)
# sig_archivo -> 0 para principal | 1 para auxiliar | -1 para el final de la cadena
RECORD_FORMAT = "i20sii"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT) # ahora es de 32 bytes

# definimos una página
PAGE_SIZE = 4096
RECORDS_PER_PAGE = PAGE_SIZE // RECORD_SIZE  # aproximadamente 128 registros por página

# creamos el header
# guarda = [registros_principal (int), registros_auxiliares (int),
#           puntero_principal (int), puntero_auxiliar (int)]
# este header nos dice dónde empezar a leer la base de datos de forma ordenada
# valores que pueden tomar:#
# 1. cant_prin (int): cantidad de registros en el ARCHIVO PRINCIPAL
#    nos sirve para saber el límite de la búsqueda binaria
# 2. cant_aux (int): cantidad de registros en el ARCHIVO AUXILIAR (overflow)
#    nos indica cuándo el archivo auxiliar llega al valor "K" y necesita un rebuild
# 3. prim_arc (int): Indica en qué archivo está el registro más pequeño de TODOS.
#    valores: 0 = archivo principal | 1 = archivo auxiliar | -1 = tabla vacía
# 4. prim_pos (int): indica la posición física (índice) del primer registro
#    si prim_arc es 0, es la posición en el principal. si es 1, es en el auxiliar
HEADER_FORMAT = "iiii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

@dataclass
class Record:
    id: int
    name: str
    next_file: int
    next_pos: int

class SeqFile:
    # 1) constructor
    def __init__(self, filename: str, k_desorted: int = 100):
        self.filename = filename
        self.aux_filename = filename.replace(".bin", "_aux.bin")
        self.k_desorted = k_desorted
        self.read_count = 0
        self.write_count = 0
        if not os.path.exists(self.filename): # si el archivo no existe
            # "w+b" = creamos el archivo permitiendo lectura y escritura binaria
            self.file = open(self.filename, "w+b")
            # inicializamos el header con 0 registros ordenados, 0 registros auxiliares
            # -1 en prim_archivo y -1 en prim_pos indica que la base de datos está vacía
            self.file.write(struct.pack(HEADER_FORMAT, 0, 0, -1, -1))
            # creamos archivo auxiliar vacío permitiendo lectura y escritura binaria
            self.aux_file = open(self.aux_filename, "w+b")
        else:
            # "r+b" = abrimos el archivo permitiendo lectura y escritura binaria sin eliminar contenido
            self.file = open(self.filename, "r+b")
            self.aux_file = open(self.aux_filename, "r+b")
            # no escribimos el header, solo lo leeremos cuando necesitemos saber cuántos registros hay

    # 2) lectura desde csv
    # cada registro guarda dos valores que indican quién es el siguiente en el orden lógico:
    # 1. next_file (int): ¿en qué archivo vive el siguiente registro con el id más cercano?
    #    valores:
    #      0 -> el siguiente está en el ARCHIVO PRINCIPAL
    #      1 -> el siguiente está en el ARCHIVO AUXILIAR (overflow)
    #     -1 -> no hay siguiente (este es el FINAL de la cadena lógica)
    # 2. next_pos (int): ¿cuál es la posición física (índice) dentro de ese archivo?
    #    ejemplo: si next_file es 1 y next_pos es 3, el siguiente registro es
    #    el que está en el índice 3 del archivo auxiliar
    @classmethod
    def from_csv(cls, filename: str, csv_path: str, k_desorted: int = 100):
        records = []
        # 1. leer los datos del csv
        with open(csv_path, newline="", encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=";")
            next(reader) # saltar encabezados del csv
            for row in reader:
                records.append(
                    Record(int(row[0]), row[1], -1 , -1))
        # 2. ordenar por id
        records.sort(key=lambda record: record.id)
        # 3. crear el archivo y limpiar
        obj = cls(filename, k_desorted)
        obj.file.seek(obj.HEADER_SIZE) # nos ubicamos en la cabecera
        obj.file.truncate() # borramos lo de abajo de donde estamos
        # 4. empaquetamos y escribimos los registros uno tras otro
        total_records = len(records)
        for i in range(total_records):
            # si no es el último, el puntero apunta al siguiente registro físico
            if i < total_records - 1:
                records[i].next_file = 0 # 0 significa archivo principal
                records[i].next_pos = i + 1
            else:
                # el último registro de la cadena apunta a -1 (final)
                records[i].next_file = -1
                records[i].next_pos = -1
            # escribir al disco (usando nuestro pack_record que ya tiene punteros)
            obj.file.write(obj._pack_record(records[i]))
            obj.write_count += 1 # opcional: contar esto como escrituras de página si lo haces en bloque
        # 5. actualizar el header
        # parametros: cant_prin, cant_aux, prim_arc, prim_pos
        # como es la primera carga, el primer registro lógico es el 0 del archivo principal
        obj._write_header(total_records, 0, 0, 0)
        return obj

    # 3) lectura de una página
    def _read_page(self, is_aux: bool, page_id: int) -> bytes:
        target_file = self.aux_file if is_aux else self.file # archivo a leer
        # calculamos el offset
        base_offset = HEADER_SIZE if not is_aux else 0
        offset = base_offset + (page_id * PAGE_SIZE)
        target_file.seek(offset) #nos ubicamos en la página
        self.read_count += 1 # contamos acceso al bloque del header
        return target_file.read(PAGE_SIZE) # leemos la página

    # 4) lectura del header
    def _read_header(self) -> Tuple[int, int, int, int]:
        self.file.seek(0) # nos ubicamos en el header
        # leemos los datos del header según su tamaño
        datos_binarios = self.file.read(self.HEADER_SIZE)
        self.read_count += 1 # contamos acceso al bloque del header
        # "unpack" = descomprimimos los bytes en una tupla
        return struct.unpack(self.HEADER_FORMAT, datos_binarios)

    # 5) escritura del header
    def _write_header(self, cant_prin: int, cant_aux: int, prim_arc: int, prim_pos: int) -> None:
        self.file.seek(0) # nos ubicamos en el header
        # "pack" = comprimimos los datos en bytes
        datos_binarios = struct.pack(self.HEADER_FORMAT, cant_prin, cant_aux, prim_arc, prim_pos)
        # los escribimos en el header
        self.file.write(datos_binarios)
        self.file.flush() # guardamos los cambios en el disco
        self.write_count += 1 # contamos acceso al bloque del header

    # 6) pack de un registro
    def _pack_record(self, rec: Record) -> bytes:
        # "pack" = comprimimos los datos en bytes
        return struct.pack(
            self.RECORD_FORMAT,
            rec.id,
            rec.name.encode("utf-8")[:20].ljust(20, b"\x00"),
            rec.next_file,
            rec.next_pos)

    # 7) unpack de un registro
    @staticmethod
    def _unpack_record(data: bytes) -> Record:
        rid, name, n_file, n_pos = struct.unpack(RECORD_FORMAT, data)
        return Record(
            id=rid,
            name=name.decode("utf-8").rstrip('\x00').strip(),
            next_file=n_file,
            next_pos=n_pos)

    # 8) calcular offset de un registro
    @staticmethod
    def _offset(index: int, is_aux: bool) -> int:
        if is_aux: # en el auxiliar empezamos desde el byte 0
            return index * RECORD_SIZE
        else: # en el principal saltamos el header
            return HEADER_SIZE + index * RECORD_SIZE

    # 9) leer un registro en una página
    def _read_record(self, index: int, is_aux: bool) -> Record:
        # calculamos en qué página está el registro
        page_id = index // RECORDS_PER_PAGE
        # traemos la página completa a RAM
        page_data = self._read_page(is_aux, page_id)
        # calculamos la posición relativa del registro dentro de la página
        pos_in_page = (index % RECORDS_PER_PAGE) * RECORD_SIZE
        record_bytes = page_data[pos_in_page: pos_in_page + RECORD_SIZE]
        # desempaquetamos los datos del registro
        return self._unpack_record(record_bytes)

    # 10) escribir un registro en una página
    def _write_record(self, index: int, is_aux: bool, rec: Record):
        # ubicamos el archivo a escribir
        target_file = self.aux_file if is_aux else self.file
        # calculamos el offset del index en el archivo y nos ubicamos
        offset = self._offset(index, is_aux)
        target_file.seek(offset)
        # empaquetamos los datos del registro y los escribimos
        target_file.write(self._pack_record(rec))
        self.write_count += 1 # contamos acceso al bloque del header

    # 11) búsqueda binaria
    def binary_search(self, id_key: int) -> Tuple[Optional[Record], int]:
        cant_prin, _, _, _ = self._read_header() # leemos el header
        # "binary search" = buscamos el registro en la zona principal/ordenada
        inicio = 0
        fin = cant_prin - 1
        last_seen_idx = -1
        # mientras nos encontramos en la zona ordenada
        while (inicio <= fin):
            # calculamos la mitad del intervalo
            mitad = (inicio + fin) // 2
            # leemos el registro de la mitad
            mid_record = self._read_record(mitad, is_aux=False)
            # lógica de comparación
            if mid_record.id == id_key: # si encontramos el registro
                return mid_record, mitad # lo devolvemos
            if mid_record.id < id_key: # si el registro de la mitad es menor que el buscado
                last_seen_idx = mitad # guardamos el menor más cercano
                inicio = mitad + 1 # seguimos buscando en la zona de la derecha
            else: # si el registro de la mitad es mayor que el buscado
                fin = mitad - 1 # seguimos buscando en la zona de la izquierda
        return None, last_seen_idx # si no encontramos el registro, devuelve None y last_seen_idx

    # 12) búsqueda
    def search(self, id_key: int) -> Optional[Record]:
        cant_prin, cant_aux, prim_arc, prim_pos = self._read_header()
        if prim_arc != -1: # si la tabla está totalmente vacía
            # leemos el primer registro lógico de la cadena
            # prim_arc == 1: true si el primer registro está en el auxiliar y a false si está en el principal
            first_rec = self._read_record(prim_pos, prim_arc==1)
            if first_rec.id == id_key: # si el id que buscamos es justo el primero de la cadena
                return first_rec # lo devolvemos
        # intentamos búsqueda binaria en el principal
        res_record, idx = self.binary_search(id_key)
        if res_record is not None:
            return res_record # si encontramos el registro, lo devolvemos
        # si no es el id exacto, el binary_search nos dio el "predecesor"
        if idx != -1: # seguimos la cadena de punteros
            current_rec = self._read_record(idx, is_aux=False)
            # mientras haya un siguiente y no nos hayamos pasado del id
            while current_rec.next_file != -1:
                # leemos el siguiente registro (ya sea en principal o ausxiliar)
                next_rec = self._read_record(current_rec.next_pos, current_rec.next_file == 1)
                if next_rec.id == id_key:
                    return next_rec
                if next_rec.id > id_key: # ya nos pasamos, no existe
                    break
                current_rec = next_rec # actualizamos el registro actual para seguir la cadena
        return None # si no encontramos el registro, devuelve None

    # 13) insertar un registro

    # 14) eliminar un registro

    # 15) búsqueda por rango

    # 16) reconstruir el archivo para integrar el área auxiliar y eliminar registros borrados

    # 17) cerrar el archivo
    def close(self):
        self.file.flush() # guardamos los cambios en el disco
        self.file.close()

    # 18) métricas para el informe y la ui
    def get_stats(self):
        return {"reads": self.read_count,
                "writes": self.write_count,
                "total_io": self.read_count + self.write_count}

    # 19) resetear las estadísticas
    def reset_stats(self):
        self.read_count = 0
        self.write_count = 0