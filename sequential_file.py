from typing import Optional, List, Tuple
from dataclasses import dataclass
import struct
import time
import csv
import os
import math
import heapq

# correcciones hechas =D

# definimos un registro (según books.csv)
# "i" = id (4 bytes) | "100s" = title (100 bytes)
# "40s" = author (40 bytes) | "i" = pages (4 bytes)
# "f" = rating (4 bytes) | "i" = year (4 bytes)
# "i" = next_file (4 bytes) | "i" = next_pos (4 bytes)
# next_file -> 0 para principal | 1 para auxiliar | -1 para el final de la cadena
RECORD_FORMAT = "i100s40sifiii"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT) # ahora es de 164 bytes

# definimos una página
PAGE_SIZE = 4096
RECORDS_PER_PAGE = PAGE_SIZE // RECORD_SIZE  # aproximadamente 24 registros por página

# creamos el header
# guarda = [registros_principal (int), registros_auxiliares (int),
#           puntero_principal (int), puntero_auxiliar (int)]
# este header nos dice dónde empezar a leer la base de datos de forma ordenada
# valores que pueden tomar:#
# 1. cant_prin (int): cantidad de registros en el ARCHIVO PRINCIPAL
#    nos sirve para saber el límite de la búsqueda binaria
# 2. cant_aux (int): cantidad de registros en el ARCHIVO AUXILIAR (overflow)
#    nos indica cuándo el archivo auxiliar llega al valor "K" y necesita un rebuild
# 3. prim_arc (int): indica en qué archivo está el registro más pequeño de TODOS
#    valores: 0 = archivo principal | 1 = archivo auxiliar | -1 = tabla vacía
# 4. prim_pos (int): indica la posición física (índice) del primer registro
#    si prim_arc es 0, es la posición en el principal. si es 1, es en el auxiliar
HEADER_FORMAT = "iiii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

@dataclass
class Record:
    id: int
    title: str
    author: str
    pages: int
    rating: float
    year: int
    next_file: int = -1
    next_pos: int = -1
def _flush_chunk(filename: str, buf: list, idx: int) -> str:
    # ordena el buffer en ram y lo escribe en un .bin temporal
    # devuelve el path del archivo temporal creado
    buf.sort(key=lambda r: r.id)
    tmp_path = filename + f".chunk_{idx}.tmp"
    with open(tmp_path, "wb") as tmp:
        for r in buf:
            # punteros en -1 porque todavía no sabemos el orden final
            r.next_file = -1
            r.next_pos = -1
            tmp.write(struct.pack(RECORD_FORMAT,
                r.id,
                r.title.encode('utf-8')[:100].ljust(100, b'\x00'),
                r.author.encode('utf-8')[:40].ljust(40, b'\x00'),
                r.pages, r.rating, r.year,
                r.next_file, r.next_pos))
    return tmp_path

def _next_from(handle) -> Optional[Record]:
    # lee el siguiente registro del archivo temporal dado
    # devuelve None si llegó al final
    raw = handle.read(RECORD_SIZE)
    if len(raw) < RECORD_SIZE:
        return None
    vals = struct.unpack(RECORD_FORMAT, raw)
    return Record(id=vals[0],
                  title=vals[1].decode('utf-8', errors='ignore').rstrip('\x00').strip(),
                  author=vals[2].decode('utf-8', errors='ignore').rstrip('\x00').strip(),
                  pages=vals[3], rating=vals[4], year=vals[5],
                  next_file=vals[6], next_pos=vals[7])
class SeqFile:
    # 1) constructor
    def __init__(self, filename: str, k_desorted):
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
    def from_csv(cls, filename: str, csv_path: str, k_desorted: int = None, limite: int = 2_000_000, chunk_size: int = 50_000):

        # fase 1: crear chunks ordenados en disco
        # un chunk es un lote de chunk_size registros que sí cabe en ram
        chunk_files = []
        chunk = []
        total_leidos = 0
        with open(csv_path, newline="", encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if total_leidos >= limite:
                    break
                try:
                    b_id = int(row["book_key"])
                    title = row["title"]
                    author = row["author"]
                    pages = int(row["pages"]) if row["pages"] else 0
                    rating = float(row["average_rating"]) if row["average_rating"] else 0.0
                    year = int(row["published_date"]) if row["published_date"] else 0
                    chunk.append(Record(id=b_id, title=title, author=author, pages=pages, rating=rating, year=year))
                    total_leidos += 1
                except (ValueError, KeyError):
                    continue # saltamos filas mal formateadas
                # cuando el buffer llega a chunk_size, lo volcamos a disco y lo limpiamos
                if len(chunk) >= chunk_size:
                    chunk_files.append(_flush_chunk(filename, chunk, len(chunk_files)))
                    chunk.clear()  # liberamos la ram del lote anterior
        # si quedaron registros sueltos que no completaron un chunk, los volcamos también
        if chunk:
            chunk_files.append(_flush_chunk(filename, chunk, len(chunk_files)))
            chunk.clear()

        # calcular k_desorted dinámicamente como log(n) donde n es el número de registros
        if k_desorted is None:
            k_desorted = int(math.log(total_leidos)) if total_leidos > 0 else 1
            print(f"k_desorted calculado dinámicamente: log({total_leidos}) = {k_desorted}")

        # fase 2: merge n-way con heapq
        # abrimos todos los temporales; el heap solo guarda un registro por chunk en ram
        handles = [open(p, "rb") for p in chunk_files]
        # inicializamos el heap con el primer registro de cada chunk
        heap = []
        for i, h in enumerate(handles):
            rec = _next_from(h)
            if rec is not None:
                # la tupla es (id, i, rec): heapq compara por id primero (orden ascendente)
                heapq.heappush(heap, (rec.id, i, rec))
        # creamos el objeto seqfile y preparamos el archivo principal limpio
        obj = cls(filename, k_desorted)
        obj.file.seek(0)
        obj.file.truncate()
        obj.aux_file.seek(0)
        obj.aux_file.truncate()
        # escribimos un header temporal vacío para reservar su espacio
        obj.file.write(struct.pack(HEADER_FORMAT, 0, 0, -1, -1))
        total_escritos = 0
        prev_rec = None # guardamos el registro anterior para actualizar su puntero
        while heap:
            # extraemos el registro con el id más pequeño de todos los chunks
            _, chunk_idx, rec = heapq.heappop(heap)
            if prev_rec is not None:
                # ahora sabemos que el siguiente de prev_rec es rec, que quedará en
                # la posición total_escritos + 1 (la siguiente a la que vamos a ocupar ahora)
                prev_rec.next_file = 0
                prev_rec.next_pos = total_escritos + 1
                obj.file.write(obj._pack_record(prev_rec))
                total_escritos += 1
            # cargamos el siguiente registro del chunk que acaba de "ganar"
            next_rec = _next_from(handles[chunk_idx])
            if next_rec is not None:
                heapq.heappush(heap, (next_rec.id, chunk_idx, next_rec))
            # guardamos rec como prev_rec; lo escribiremos en la siguiente iteración
            # cuando ya sepamos a quién apunta
            prev_rec = rec
        # el último registro de la cadena apunta a -1 (fin de cadena)
        if prev_rec is not None:
            prev_rec.next_file = -1
            prev_rec.next_pos = -1
            obj.file.write(obj._pack_record(prev_rec))
            total_escritos += 1
        # cerramos y borramos todos los archivos temporales
        for h in handles:
            h.close()
        for p in chunk_files:
            os.remove(p)
        # contamos páginas escritas (el merge escribe directo sin pasar por _write_record)
        obj.write_count += math.ceil(total_escritos / RECORDS_PER_PAGE)
        obj._write_header(total_escritos, 0, 0, 0)
        return obj

    # 3) lectura de una página
    def _read_page(self, is_aux: bool, page_id: int) -> bytes:
        target_file = self.aux_file if is_aux else self.file  # archivo a leer
        # calculamos el offset
        base_offset = HEADER_SIZE if not is_aux else 0
        offset = base_offset + (page_id * PAGE_SIZE)
        # verificamos el tamaño del archivo para no leer fuera de los límites
        target_file.seek(0, 2)
        file_size = target_file.tell()
        if offset >= file_size:
            return b"\x00" * PAGE_SIZE # si está fuera de rango, devolvemos bytes vacíos
        target_file.seek(offset) # nos ubicamos en la página
        self.read_count += 1 # contamos acceso al bloque del header
        data = target_file.read(PAGE_SIZE) # leemos la página
        # si la lectura fue incompleta (final del archivo), rellenamos con ceros hasta completar PAGE_SIZE
        if len(data) < PAGE_SIZE:
            data = data.ljust(PAGE_SIZE, b"\x00")
        return data

    # 4) lectura del header
    def _read_header(self) -> Tuple[int, int, int, int]:
        self.file.seek(0) # nos ubicamos en el header
        # leemos los datos del header según su tamaño
        datos_binarios = self.file.read(HEADER_SIZE)
        self.read_count += 1 # contamos acceso al bloque del header
        # "unpack" = descomprimimos los bytes en una tupla
        return struct.unpack(HEADER_FORMAT, datos_binarios)

    # 5) escritura del header
    def _write_header(self, cant_prin: int, cant_aux: int, prim_arc: int, prim_pos: int) -> None:
        self.file.seek(0) # nos ubicamos en el header
        # "pack" = comprimimos los datos en bytes
        datos_binarios = struct.pack(HEADER_FORMAT, cant_prin, cant_aux, prim_arc, prim_pos)
        # los escribimos en el header
        self.file.write(datos_binarios)
        self.file.flush() # guardamos los cambios en el disco
        self.write_count += 1 # contamos acceso al bloque del header

    # 6) pack de un registro
    @staticmethod
    def _pack_record(rec: Record) -> bytes:
        # "pack" = comprimimos los datos en bytes
        return struct.pack(
            RECORD_FORMAT,
            rec.id,
            rec.title.encode('utf-8')[:100].ljust(100, b'\x00'),
            rec.author.encode('utf-8')[:40].ljust(40, b'\x00'),
            rec.pages,
            rec.rating,
            rec.year,
            rec.next_file,
            rec.next_pos)

    # 7) unpack de un registro
    @staticmethod
    def _unpack_record(data: bytes) -> Record:
        # descomprimimos todos los campos
        values = struct.unpack(RECORD_FORMAT, data)
        return Record(
            id=values[0],
            title=values[1].decode('utf-8', errors="ignore").rstrip('\x00').strip(),
            author=values[2].decode('utf-8', errors="ignore").rstrip('\x00').strip(),
            pages=values[3],
            rating=values[4],
            year=values[5],
            next_file=values[6],
            next_pos=values[7])

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
        target_file.flush()
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
        if prim_arc != -1:  # si la tabla está totalmente vacía
            # leemos el primer registro lógico de la cadena
            # prim_arc == 1: true si el primer registro está en el auxiliar y a false si está en el principal
            first_rec = self._read_record(prim_pos, prim_arc == 1)
            if first_rec.id == id_key:  # si el id que buscamos es justo el primero de la cadena
                return first_rec  # lo devolvemos
        # intentamos búsqueda binaria en el principal
        res_record, idx = self.binary_search(id_key)
        if res_record is not None:
            return res_record  # si encontramos el registro, lo devolvemos
        # si no es el id exacto, el binary_search nos dio el "predecesor"
        if idx != -1:  # seguimos la cadena de punteros
            current_rec = self._read_record(idx, is_aux=False)
            # calculamos el máximo de pasos posibles para no colgarnos si hay un puntero corrupto
            max_pasos = cant_prin + cant_aux
            pasos = 0
            # cortamos cuando superamos el total de registros posibles
            while current_rec.next_file != -1 and pasos < max_pasos:
                # leemos el siguiente registro (ya sea en principal o auxiliar)
                next_rec = self._read_record(current_rec.next_pos, current_rec.next_file == 1)
                if next_rec.id == id_key:
                    return next_rec
                if next_rec.id > id_key:  # ya nos pasamos, no existe
                    break
                current_rec = next_rec  # actualizamos el registro actual para seguir la cadena
                pasos += 1
        return None  # si no encontramos el registro, devuelve None

    # 13) insertar un registro
    def add(self, record: Record):
        # leemos el header para conocer el estado actual
        cant_prin, cant_aux, prim_arc, prim_pos = self._read_header()
        # caso a: la tabla está vacía
        if prim_arc == -1:
            # el primer registro no tiene a nadie después de él
            record.next_file = -1
            record.next_pos = -1
            # lo guardamos en la primera posición del archivo principal
            self._write_record(0, is_aux=False, rec=record)
            # actualizamos el header: 1 en principal, 0 en aux, y la cadena empieza en (0,0)
            self._write_header(1, 0, 0, 0)
            return
        # buscamos el índice del registro con el id más cercano (menor) al que queremos insertar
        _, pred_idx = self.binary_search(record.id)
        # caso b: el nuevo registro tiene el id más pequeño de toda la tabla (su predecesor no existe)
        if pred_idx == -1:
            # el nuevo registro apunta al que antes era el primero
            record.next_file = prim_arc
            record.next_pos = prim_pos
            # guardamos el nuevo registro al final del archivo auxiliar
            self._write_record(cant_aux, is_aux=True, rec=record)
            # el header ahora dice que el inicio de la tabla está en el auxiliar
            self._write_header(cant_prin, cant_aux + 1, 1, cant_aux)
            # caso c: inserción normal en medio o al final de la cadena
        else:
            # variables para recordar dónde está físicamente el predecesor
            curr_arc = 0 # empezamos asumiendo que está en principal (por la búsqueda binaria)
            curr_pos = pred_idx
            # leemos el registro que la búsqueda binaria marcó como posible predecesor
            actual = self._read_record(curr_pos, is_aux=False)
            # avanzamos por la cadena de punteros si el predecesor apunta al archivo auxiliar
            max_pasos = cant_prin + cant_aux
            pasos = 0
            while actual.next_file != -1 and pasos < max_pasos:
                # leemos el siguiente registro en la secuencia lógica
                sig_temp = self._read_record(actual.next_pos, actual.next_file == 1)
                # si el siguiente ya es mayor que nuestro nuevo id, aquí es donde debemos insertar
                if sig_temp.id >= record.id:
                    break
                # si no, actualizamos el rastro de ubicación y seguimos avanzando
                curr_arc = actual.next_file
                curr_pos = actual.next_pos
                actual = sig_temp
                pasos += 1
            # "actual" es ahora nuestro predecesor inmediato
            # el nuevo registro hereda el puntero (el "hijo") que tenía su predecesor
            record.next_file = actual.next_file
            record.next_pos = actual.next_pos
            # guardamos físicamente el nuevo registro al final del archivo auxiliar
            new_pos_in_aux = cant_aux
            self._write_record(new_pos_in_aux, is_aux=True, rec=record)
            # el predecesor ahora debe apuntar al nuevo registro que acabamos de guardar
            actual.next_file = 1 # 1 indica que ahora el siguiente está en el auxiliar
            actual.next_pos = new_pos_in_aux
            # sobreescribimos el predecesor en su posición original con el puntero actualizado
            self._write_record(curr_pos, is_aux=(curr_arc == 1), rec=actual)
            # actualizamos el header sumando uno al conteo de registros auxiliares
            self._write_header(cant_prin, cant_aux + 1, prim_arc, prim_pos)
        # verificamos si el archivo auxiliar llegó al límite permitido para el desorden
        if (cant_aux + 1) >= self.k_desorted:
            self.rebuild() # si es necesario, aplicar reconstrucción

    # 14) eliminar un registro
    def remove(self, id_key: int) -> bool:
        # utilizamos la función search que busca en el principal y en el auxiliar siguiendo el orden lógico
        registro_encontrado = self.search(id_key)
        if registro_encontrado is None:
            return False # si el registro no existe en la cadena, no hay nada que eliminar
        # para eliminarlo lógicamente, cambiamos su id a -1
        registro_encontrado.id = -1
        # necesitamos saber dónde está físicamente para sobrescribirlo
        # lo buscamos en el archivo principal con búsqueda binaria
        res_bin, idx_p = self.binary_search(id_key)
        # verificamos si la búsqueda binaria nos dio el registro exacto
        if res_bin is not None and res_bin.id == id_key:
            # sobreescribimos con su versión marcada como borrado
            self._write_record(idx_p, is_aux=False, rec=registro_encontrado)
            return True # confirmamos que la eliminación fue exitosa y salimos
        # si no estaba en el principal, tiene que estar en el auxiliar
        # recorremos la cadena desde el inicio usando el header
        cant_prin, cant_aux, p_arc, p_pos = self._read_header()
        # inicializamos el rastro con el primer registro que nos indica el header
        curr_arc = p_arc # curr_arc será 0 si empieza en principal o 1 si empieza en auxiliar
        curr_pos = p_pos # curr_pos es el índice físico donde está el primer registro
        # cortamos con max_pasos como techo seguro
        max_pasos = cant_prin + cant_aux
        pasos = 0
        while curr_arc != -1 and pasos < max_pasos:
            # leemos el registro de la posición actual (is_aux es true si curr_arc es 1)
            rec = self._read_record(curr_pos, is_aux=(curr_arc == 1))
            # comparamos si el registro que tenemos es el que queremos eliminar
            if rec.id == id_key:
                # si lo encontramos, lo sobreescribimos con su versión marcada como borrado
                self._write_record(curr_pos, is_aux=(curr_arc == 1), rec=registro_encontrado)
                return True  # confirmamos que la eliminación fue exitosa y salimos
            # si no es el buscado, leemos sus punteros para saltar al siguiente registro
            # actualizamos el archivo (principal o auxiliar) para la siguiente iteración
            curr_arc = rec.next_file
            # actualizamos la posición física para la siguiente iteración
            curr_pos = rec.next_pos
            pasos += 1
        return False  # si el registro no existe físicamente, retorna False

    # 15) búsqueda por rango
    def range_search(self, begin: int, end: int) -> List[Record]:
        resultados = []
        # usamos el binary_search para encontrar dónde empezaría el rango
        # nos interesa el "menor más cercano" (idx) si el id exacto no está
        _, idx = self.binary_search(begin)
        # determinamos el punto de inicio real (si idx es -1, empezamos desde el header)
        cant_prin, cant_aux, curr_arc, curr_pos = self._read_header()
        # si la búsqueda binaria encontró un predecesor, empezamos desde ahí
        if idx != -1:
            # verificamos que el predecesor no sea mayor que begin antes de usarlo
            # (binary_search devuelve el último visto menor, pero puede ser >= begin)
            pred_rec = self._read_record(idx, is_aux=False)
            if pred_rec.id < begin:
                # el predecesor es menor que begin: empezamos desde él y avanzamos
                curr_arc = 0
                curr_pos = idx
            elif pred_rec.id >= begin:
                # el predecesor ya está dentro del rango, empezamos desde él
                curr_arc = 0
                curr_pos = idx
        # recorremos la cadena lógica saltando entre archivos
        while curr_arc != -1:
            # leemos el registro actual (esto suma +1 a tus lecturas de página)
            rec = self._read_record(curr_pos, is_aux=(curr_arc == 1))
            # si el id ya superó el límite superior del rango, podemos dejar de buscar
            # esto es gracias a que la cadena siempre está ordenada lógicamente
            if rec.id > end:
                break
            # si el id del registro está dentro del rango, lo agregamos
            # solo agregamos registros que no estén marcados como borrados (-1)
            if begin <= rec.id <= end and rec.id != -1:
                resultados.append(rec)
            # avanzamos al siguiente registro siguiendo el puntero
            curr_arc = rec.next_file
            curr_pos = rec.next_pos
        return resultados

    # 16) reconstruir el archivo para integrar el área auxiliar y eliminar registros borrados
    def rebuild(self):
        # leemos el header para saber dónde empezar a seguir la cadena
        cant_prin, cant_aux, prim_arc, prim_pos = self._read_header()
        total_esperado = cant_prin + cant_aux  # sumamos para saber cuántos hay en total
        # creamos un nombre para un archivo temporal donde contruiremos la nueva versión
        temp_filename = self.filename + ".tmp"
        # creamos un contador que nos dirá cuántos registros reales (no borrados) quedaron
        new_records = 0
        # abrimos el archivo remporal en modo escritura binaria
        with open(temp_filename, "wb") as temp_file:
            # dejamos el espacio inicial para el nuevo header
            temp_file.write(struct.pack(HEADER_FORMAT, 0, 0, 0, 0))
            # empezamos el recorrido desde el primer registro lógico de la cadena
            curr_arc = prim_arc
            curr_pos = prim_pos
            ultimo_rec_valido = None # para guardar el último objeto Record escrito
            # mientras no lleguemos al final de la cadena de punteros
            # limitamos con total_esperado para evitar bucles infinitos por punteros corruptos
            registros_procesados = 0
            while curr_arc != -1 and registros_procesados < total_esperado:
                # leemos el registro actual usando la función
                record = self._read_record(curr_pos, is_aux=(curr_arc == 1))
                registros_procesados += 1
                # guardamos los punteros originales antes de que el objeto "record" sea modificado
                next_f_temp = record.next_file
                next_p_temp = record.next_pos
                # si el registro no está marcado como eliminado
                if record.id != -1:
                    # el nuevo archivo estará fisicamente ordenado
                    # el siguiente registro estará justo en la posición de adelante
                    record.next_file = 0
                    record.next_pos = new_records + 1
                    # empaquetamos el registro y lo escribimos en el archivo temporal
                    temp_file.write(self._pack_record(record))
                    new_records += 1
                    # contamos la escritura para las métricas
                    self.write_count += 1
                    ultimo_rec_valido = record  # guardamos este para el final de la cadena
                # saltamos al siguiente registro en la secuencia lógica usando los temporales
                curr_arc = next_f_temp
                curr_pos = next_p_temp
            # corrección del último puntero: debe apuntar a -1
            if new_records > 0 and ultimo_rec_valido is not None:
                # nos ubicamos al inicio del último registro escrito
                # para eso, saltamos el header y (n-1) registros
                last_record_offset = HEADER_SIZE + (new_records - 1) * RECORD_SIZE
                temp_file.seek(last_record_offset)
                # sobreescribimos los punteros
                ultimo_rec_valido.next_file = -1
                ultimo_rec_valido.next_pos = -1
                # empaquetamos el registro y lo escribimos en el archivo temporal
                temp_file.write(self._pack_record(ultimo_rec_valido))
            # actualizamos el header del archivo temporal
            temp_file.seek(0)
            temp_file.write(struct.pack(HEADER_FORMAT, new_records, 0, 0, 0))
        # cerramos las conexiones actuales para poder reemplazar el archivo principal
        self.file.close()
        self.aux_file.close()
        # reemplazamos el archivo principal por el temporal
        os.replace(temp_filename, self.filename)
        # vaciamos el archivo auxiliar (lo dejamos en 0 bytes)
        with open(self.aux_filename, "wb") as f:
            pass
        # reabrimos ambos archivos para que sean accesibles de nuevo
        self.file = open(self.filename, "r+b")
        self.aux_file = open(self.aux_filename, "r+b")
        # dejamos los contadores sin resetear para ver cuánto costó el mantenimiento

    # 17) cerrar el archivo
    def close(self):
        self.file.flush() # guardamos los cambios en el disco
        self.file.close()

    # 18) métricas para el informe y la ui
    def get_stats(self, last_op_time: float=0):
        return {"tiempo de ejecución (ms)": last_op_time,
                "reads": self.read_count,
                "writes": self.write_count,
                "total_io": self.read_count + self.write_count}

    # 19) resetear las estadísticas
    def reset_stats(self):
        self.read_count = 0
        self.write_count = 0

def ejecutar_y_medir(nombre_op, operacion, db_instance):
    db_instance.reset_stats()
    start_time = time.time()
    resultado = operacion()
    end_time = time.time()
    # calcular métricas
    tiempo_ms = (end_time - start_time) * 1000
    stats = db_instance.get_stats()
    print(f"\n--- reporte de {nombre_op} ---")
    print(f"tiempo: {tiempo_ms:.4f} ms")
    print(f"accesos a disco (páginas read+write): {stats['total_io']}")
    return resultado

def main():
    # nombres de archivos
    nombre_bin = "books_data.bin"
    nombre_csv = "books.csv"

    # 1. carga inicial (simulando CREATE TABLE FROM FILE)
    print("--- 1. probando carga inicial desde books.csv ---")
    # k_desorted=5 para ver el rebuild rápido si insertamos varios
    db = SeqFile.from_csv(nombre_bin, nombre_csv, k_desorted=5, limite=50)
    print(f"carga completa. stats de creación: {db.get_stats()}")

    # 2. probar búsqueda individual (buscamos el libro id 6 del csv)
    res = ejecutar_y_medir("búsqueda id 6", lambda: db.search(6), db)
    if res:
        print(f"libro encontrado: {res.title} | autor: {res.author} | rating: {res.rating}")
    else:
        print("libro no encontrado")

    # 3. probar inserción (añadimos un libro nuevo que no esté en el csv)
    nuevo_libro = Record(
        id=500,
        title="enhypen yey",
        author="mafer",
        pages=7,
        rating=5.0,
        year=2020)
    ejecutar_y_medir("inserción libro id 500", lambda: db.add(nuevo_libro), db)

    # 4. probar búsqueda por rango (libros con id entre 1 y 5)
    print("\n--- buscando libros en rango id [1 - 5] ---")
    resultados = ejecutar_y_medir("rango 1-5", lambda: db.range_search(1, 5), db)
    for libro in resultados:
        print(f"  > [id {libro.id}] {libro.title}")

    # 5. probar eliminación lógica (eliminamos el libro con id 500)
    ejecutar_y_medir("eliminación id 500", lambda: db.remove(500), db)

    # verificamos que ya no existe
    res_deleted = db.search(500)
    print(f"¿existe el id 500 después de borrar?: {'sí' if res_deleted else 'no (borrado lógico)'}")

    # 6. forzar rebuild (insertamos más libros para superar k_desorted=5)
    print("\n--- insertando más libros para forzar rebuild físico ---")
    for i in range(30, 35):
        db.add(Record(i, f"libro extra {i}", "autor x", 100, 4.0, 2022))

    stats_final = db.get_stats()
    print(f"\nestadísticas acumuladas finales: {stats_final}")

    db.close()
    print("\npruebas finalizadas con éxito")

if __name__ == "__main__":
    main()