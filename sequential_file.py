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
                    pages = int(float(row["pages"])) if row["pages"] else 0
                    rating = float(row["average_rating"]) if row["average_rating"] else 0.0
                    year = int(float(row["published_date"])) if row["published_date"] else 0
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
        print(f"Total de registros leídos desde CSV: {total_leidos}")

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
                # la posición total_escritos + 1 (la siguiente posición donde vamos a escribir rec)
                prev_rec.next_file = 0
                prev_rec.next_pos = total_escritos + 1  # CORRECCIÓN: siguiente estará en la siguiente posición
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
        # el primer registro está en posición 0 (los registros están ordenados por ID)
        obj._write_header(total_escritos, 0, 0, 0)
        
        # mostrar primeros IDs para debugging
        print("Primeros 5 IDs cargados:")
        for i in range(min(5, total_escritos)):
            rec = obj._read_record(i, is_aux=False)
            print(f"  ID: {rec.id}")
        
        return obj

    # 3) lectura de una página
    def _read_page(self, is_aux: bool, page_id: int) -> bytes:
        target_file = self.aux_file if is_aux else self.file
        # el archivo se escribe de forma contigua (sin padding entre páginas)
        # cada "página lógica" contiene RECORDS_PER_PAGE registros consecutivos
        base_offset = HEADER_SIZE if not is_aux else 0
        offset = base_offset + page_id * RECORDS_PER_PAGE * RECORD_SIZE
        target_file.seek(0, 2)
        file_size = target_file.tell()
        if offset >= file_size:
            return b"\x00" * PAGE_SIZE
        target_file.seek(offset)
        self.read_count += 1
        data = target_file.read(RECORDS_PER_PAGE * RECORD_SIZE)
        if len(data) < RECORDS_PER_PAGE * RECORD_SIZE:
            data = data.ljust(RECORDS_PER_PAGE * RECORD_SIZE, b"\x00")
        return data

    # 4) lectura del header
    def _read_header(self) -> Tuple[int, int, int, int]:
        self.file.seek(0) # nos ubicamos en el header
        # leemos los datos del header según su tamaño
        datos_binarios = self.file.read(HEADER_SIZE)
        self.read_count += 1 # contamos acceso al bloque del header
        # asegurarnos que tenemos suficientes bytes para unpack
        if len(datos_binarios) < HEADER_SIZE:
            datos_binarios = datos_binarios.ljust(HEADER_SIZE, b'\x00')
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
        page_id = index // RECORDS_PER_PAGE
        page_data = self._read_page(is_aux, page_id)
        pos_in_page = (index % RECORDS_PER_PAGE) * RECORD_SIZE
        record_bytes = page_data[pos_in_page: pos_in_page + RECORD_SIZE]
        return self._unpack_record(record_bytes)

    # 10) escribir un registro en una página
    def _write_record(self, index: int, is_aux: bool, rec: Record):
        # ubicamos el archivo a escribir
        target_file = self.aux_file if is_aux else self.file
        # calculamos el offset del index en el archivo y nos ubicamos
        offset = self._offset(index, is_aux)
        target_file.seek(offset)
        # empaquetamos los datos del registro y los escribimos
        packed_data = self._pack_record(rec)
        # asegurarnos que escribamos exactamente RECORD_SIZE bytes
        if len(packed_data) < RECORD_SIZE:
            packed_data = packed_data.ljust(RECORD_SIZE, b'\x00')
        target_file.write(packed_data)
        target_file.flush()
        self.write_count += 1 # contamos acceso al bloque del header

    # 11) búsqueda binaria
    def binary_search(self, id_key: int) -> Tuple[Optional[Record], int]:
        """
        Busca id_key en el archivo principal ordenado.
        Retorna (record, idx) si lo encuentra exacto, o (None, last_menor_idx) si no.
        Ignora registros borrados (id == -1) moviéndose hacia la izquierda,
        pero solo cuando el borrado está en la posición exacta del mid.
        Para evitar falsos negativos, si mid tiene id=-1 buscamos en ambas
        direcciones usando el predecesor más cercano.
        """
        cant_prin, _, _, _ = self._read_header()
        inicio = 0
        fin = cant_prin - 1
        last_seen_idx = -1
        while inicio <= fin:
            mitad = (inicio + fin) // 2
            mid_record = self._read_record(mitad, is_aux=False)
            if mid_record.id == id_key:
                return mid_record, mitad
            if mid_record.id == -1:
                # registro borrado: no sabemos la dirección, buscar el vecino izquierdo
                # para determinar si id_key está a la derecha
                left = mitad - 1
                while left >= inicio:
                    lr = self._read_record(left, is_aux=False)
                    if lr.id != -1:
                        break
                    left -= 1
                if left >= inicio and self._read_record(left, is_aux=False).id < id_key:
                    last_seen_idx = left
                    inicio = mitad + 1
                else:
                    fin = mitad - 1
                continue
            if mid_record.id < id_key:
                last_seen_idx = mitad
                inicio = mitad + 1
            else:
                fin = mitad - 1
        return None, last_seen_idx

    # 12) búsqueda
    def search(self, id_key: int) -> Optional[Record]:
        cant_prin, cant_aux, prim_arc, prim_pos = self._read_header()
        if prim_arc == -1:
            return None  # tabla vacía

        # caso especial: el id buscado es el primero de la cadena lógica
        first_rec = self._read_record(prim_pos, prim_arc == 1)
        if first_rec.id == id_key:
            return first_rec

        # binary search en el archivo principal ordenado
        res_record, idx = self.binary_search(id_key)
        if res_record is not None:
            return res_record  # encontrado directamente

        # si no está en el principal, seguir la cadena desde el predecesor
        if idx != -1:
            current_rec = self._read_record(idx, is_aux=False)
            max_pasos = cant_aux + 1  # solo hay que recorrer el auxiliar
            pasos = 0
            while current_rec.next_file != -1 and pasos < max_pasos:
                next_rec = self._read_record(current_rec.next_pos, current_rec.next_file == 1)
                if next_rec.id == id_key:
                    return next_rec
                if next_rec.id > id_key:
                    break
                current_rec = next_rec
                pasos += 1
        return None

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
        else:
            # caso c: inserción normal en medio o al final de la cadena
            # variables para recordar dónde está físicamente el predecesor
            curr_arc = 0 # empezamos asumiendo que está en principal (por la búsqueda binaria)
            curr_pos = pred_idx
            # leemos el registro que la búsqueda binaria marcó como posible predecesor
            actual = self._read_record(curr_pos, is_aux=False)
            
            # avanzamos por la cadena de punteros para encontrar el verdadero predecesor
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
        registro_encontrado = self.search(id_key)
        if registro_encontrado is None:
            return False

        cant_prin, cant_aux, prim_arc, prim_pos = self._read_header()

        # guardar next antes de marcar como borrado
        next_file_orig = registro_encontrado.next_file
        next_pos_orig  = registro_encontrado.next_pos

        # marcar como borrado
        registro_encontrado.id = -1

        # buscar posición física en el principal
        res_bin, idx_p = self.binary_search(id_key)

        if res_bin is not None and res_bin.id == id_key:
            # está en el principal
            self._write_record(idx_p, is_aux=False, rec=registro_encontrado)
            # si era el primer registro lógico, actualizar el header
            if prim_arc == 0 and prim_pos == idx_p:
                self._write_header(cant_prin, cant_aux, next_file_orig, next_pos_orig)
            return True

        # está en el auxiliar — recorrer la cadena para encontrarlo
        curr_arc = prim_arc
        curr_pos = prim_pos
        max_pasos = cant_prin + cant_aux
        pasos = 0
        while curr_arc != -1 and pasos < max_pasos:
            rec = self._read_record(curr_pos, is_aux=(curr_arc == 1))
            if rec.id == id_key:
                self._write_record(curr_pos, is_aux=(curr_arc == 1), rec=registro_encontrado)
                # si era el primer registro lógico, actualizar el header
                if curr_arc == prim_arc and curr_pos == prim_pos:
                    self._write_header(cant_prin, cant_aux, next_file_orig, next_pos_orig)
                return True
            curr_arc = rec.next_file
            curr_pos = rec.next_pos
            pasos += 1
        return False

    # 15) búsqueda por rango
    def range_search(self, begin: int, end: int) -> List[Record]:
        resultados = []
        # Empezar desde el primer registro lógico según el header
        cant_prin, cant_aux, curr_arc, curr_pos = self._read_header()
        
        # Si la tabla está vacía, devolver lista vacía
        if curr_arc == -1:
            return resultados
            
        max_pasos = cant_prin + cant_aux
        pasos = 0
        
        while curr_arc != -1 and pasos < max_pasos:
            pasos += 1
            # leemos el registro actual
            rec = self._read_record(curr_pos, is_aux=(curr_arc == 1))
            
            # si el id ya superó el límite superior del rango, podemos dejar de buscar
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

    # 17) cerrar archivos
    def close(self):
        """Cierra los archivos principal y auxiliar"""
        if hasattr(self, 'file') and self.file:
            self.file.close()
        if hasattr(self, 'aux_file') and self.aux_file:
            self.aux_file.close()

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
    print("=== PRUEBAS COMPLETAS DE SEQUENTIAL_FILE.py CON TODOS LOS CSVs ===\n")
    
    # Test con books_1000.csv
    print("1. Probando con books_1000.csv (1,000 registros)...")
    db1 = test_csv("books_1000.csv", "books_data_1000.bin", limite=1000)
    
    # Test con books_10000.csv
    print("2. Probando con books_10000.csv (10,000 registros)...")
    db2 = test_csv("books_10000.csv", "books_data_10000.bin", limite=10000)
    
    # Test con books_100000.csv
    print("3. Probando con books_100000.csv (100,000 registros)...")
    db3 = test_csv("books_100000.csv", "books_data_100000.bin", limite=100000)
    
    # Test con books_1000000.csv
    print("4. Probando con books_1000000.csv (1,000,000 registros)...")
    db4 = test_csv("books_1000000.csv", "books_data_1000000.bin", limite=1000000)
    
    print("\n=== TODAS LAS PRUEBAS COMPLETADAS CON ÉXITO ===")

def test_csv(csv_path, bin_path, limite):
    """Función auxiliar para probar un CSV específico"""
    try:
        # k_desorted dinámico basado en el logaritmo del número de registros
        k_desorted = max(5, int(math.log(limite)))
        
        print(f"   Cargando {limite} registros con k_desorted={k_desorted}...")
        db = SeqFile.from_csv(bin_path, f"books/{csv_path}", k_desorted=k_desorted, limite=limite)
        
        # Probar búsqueda
        print(f"   ✓ Carga completa: {db.get_stats()}")
        
        # Probar búsqueda individual
        test_busqueda(db, 3523)  # ID que sabemos que existe
        
        # Probar búsqueda por rango
        test_rango(db, 3500, 4000)
        
        # Probar inserción
        test_insercion(db)
        
        # Probar eliminación
        test_eliminacion(db)
        
        # Cerrar y limpiar
        db.close()
        print(f"   ✓ Tests completados para {csv_path}")
        
        return db
        
    except Exception as e:
        print(f"   ✗ Error en {csv_path}: {e}")
        return None

def test_busqueda(db, id_buscar):
    """Prueba búsqueda individual"""
    res = db.search(id_buscar)
    if res:
        print(f"   ✓ Búsqueda ID {id_buscar}: {res.title[:30]}... | {res.author}")
    else:
        print(f"   ✗ Búsqueda ID {id_buscar}: NO ENCONTRADO")

def test_rango(db, inicio, fin):
    """Prueba búsqueda por rango"""
    resultados = db.range_search(inicio, fin)
    print(f"   ✓ Rango [{inicio}-{fin}]: {len(resultados)} resultados encontrados")
    if len(resultados) > 0:
        print(f"   Primer resultado: ID {resultados[0].id} - {resultados[0].title[:30]}...")
    else:
        print("   ✗ Sin resultados en rango")

def test_insercion(db):
    """Prueba inserción"""
    nuevo_libro = Record(id=999999, title="Libro Test", author="Autor Test", pages=100, rating=4.5, year=2023)
    db.add(nuevo_libro)
    if db.search(999999):
        print("   ✓ Inserción: EXITOSA")
    else:
        print("   ✗ Inserción: FALLÓ")

def test_eliminacion(db):
    """Prueba eliminación"""
    if db.search(999999):
        db.remove(999999)
        if not db.search(999999):
            print("   ✓ Eliminación: EXITOSA")
        else:
            print("   ✗ Eliminación: FALLÓ")
    else:
        print("   ✗ Eliminación: REGISTRO NO EXISTE")

if __name__ == "__main__":
    main()