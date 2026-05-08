from dataclasses import dataclass, field
from typing import List, Optional, Any
from enum import Enum, auto

class TipoToken(Enum):
    SELECT=auto(); FROM=auto(); WHERE=auto(); INSERT=auto(); INTO=auto()
    VALUES=auto(); DELETE=auto(); CREATE=auto(); TABLE=auto(); INDEX=auto()
    BETWEEN=auto(); AND=auto(); IN=auto(); POINT=auto(); RADIUS=auto()
    FILE=auto(); K=auto(); LIMIT=auto()
    STAR=auto(); EQ=auto(); COMMA=auto(); LPAREN=auto(); RPAREN=auto(); SEMICOL=auto()
    NUMBER=auto(); STRING=auto(); IDENT=auto()
    END=auto(); ERR=auto()

@dataclass
class Token:
    tipo: TipoToken
    valor: Any = None
    def __repr__(self): return f"Token({self.tipo.name}, {self.valor!r})"

KEYWORDS = {
    "select":TipoToken.SELECT,"from":TipoToken.FROM,"where":TipoToken.WHERE,
    "insert":TipoToken.INSERT,"into":TipoToken.INTO,"values":TipoToken.VALUES,
    "delete":TipoToken.DELETE,"create":TipoToken.CREATE,"table":TipoToken.TABLE,
    "index":TipoToken.INDEX,"between":TipoToken.BETWEEN,"and":TipoToken.AND,
    "in":TipoToken.IN,"point":TipoToken.POINT,"radius":TipoToken.RADIUS,
    "file":TipoToken.FILE,"k":TipoToken.K,"limit":TipoToken.LIMIT,
}

class Scanner:
    def __init__(self, texto):
        self.texto=texto; self.pos=0; self.tokens=[]
        self._tokenizar()

    def _tokenizar(self):
        while self.pos < len(self.texto):
            self._skip()
            if self.pos >= len(self.texto): break
            c = self.texto[self.pos]
            if c.isdigit() or (c=='-' and self.pos+1<len(self.texto) and self.texto[self.pos+1].isdigit()):
                self.tokens.append(self._num())
            elif c.isalpha() or c=='_': self.tokens.append(self._ident())
            elif c=='"': self.tokens.append(self._string())
            elif c=='*': self.tokens.append(Token(TipoToken.STAR,'*')); self.pos+=1
            elif c=='=': self.tokens.append(Token(TipoToken.EQ,'=')); self.pos+=1
            elif c==',': self.tokens.append(Token(TipoToken.COMMA,',')); self.pos+=1
            elif c=='(': self.tokens.append(Token(TipoToken.LPAREN,'(')); self.pos+=1
            elif c==')': self.tokens.append(Token(TipoToken.RPAREN,')')); self.pos+=1
            elif c==';': self.tokens.append(Token(TipoToken.SEMICOL,';')); self.pos+=1
            else: self.tokens.append(Token(TipoToken.ERR,c)); self.pos+=1
        self.tokens.append(Token(TipoToken.END))

    def _skip(self):
        while self.pos<len(self.texto) and self.texto[self.pos] in ' \t\n\r': self.pos+=1

    def _num(self):
        i=self.pos
        if self.texto[self.pos]=='-': self.pos+=1
        while self.pos<len(self.texto) and self.texto[self.pos].isdigit(): self.pos+=1
        if self.pos<len(self.texto) and self.texto[self.pos]=='.':
            self.pos+=1
            while self.pos<len(self.texto) and self.texto[self.pos].isdigit(): self.pos+=1
            return Token(TipoToken.NUMBER, float(self.texto[i:self.pos]))
        return Token(TipoToken.NUMBER, int(self.texto[i:self.pos]))

    def _ident(self):
        i=self.pos
        while self.pos<len(self.texto) and (self.texto[self.pos].isalnum() or self.texto[self.pos]=='_'): self.pos+=1
        lex=self.texto[i:self.pos]
        return Token(KEYWORDS.get(lex.lower(), TipoToken.IDENT), lex)

    def _string(self):
        self.pos+=1; i=self.pos
        while self.pos<len(self.texto) and self.texto[self.pos]!='"': self.pos+=1
        s=self.texto[i:self.pos]; self.pos+=1
        return Token(TipoToken.STRING, s)

# ── AST nodes ──────────────────────────────────────────────────────────────────
@dataclass
class NodoColumna:
    nombre:str; tipo:str; indice:Optional[str]=None

@dataclass
class NodoCreateTable:
    tabla:str; columnas:List[NodoColumna]; archivo:Optional[str]=None

@dataclass
class NodoSelectPuntual:
    tabla:str; columna:str; valor:Any

@dataclass
class NodoSelectRango:
    tabla:str; columna:str; inicio:Any; fin:Any

@dataclass
class NodoSelectTodos:
    tabla:str; limite:Optional[int]=None

@dataclass
class NodoSelectRadio:
    tabla:str; columna:str; x:float; y:float; radio:float

@dataclass
class NodoSelectKNN:
    tabla:str; columna:str; x:float; y:float; k:int

@dataclass
class NodoInsert:
    tabla:str; valores:List[Any]

@dataclass
class NodoDelete:
    tabla:str; columna:str; valor:Any

@dataclass
class NodoPrograma:
    sentencias:List[Any]=field(default_factory=list)

# ── Parser ─────────────────────────────────────────────────────────────────────
class Parser:
    def __init__(self, tokens):
        self.tokens=tokens; self.pos=0
        self.current=tokens[0]; self.prev=None

    def _end(self): return self.current.tipo==TipoToken.END
    def _check(self,t): return self.current.tipo==t
    def _advance(self):
        self.prev=self.current; self.pos+=1; self.current=self.tokens[self.pos]
        return self.prev
    def _match(self,*tipos):
        for t in tipos:
            if self._check(t): self._advance(); return True
        return False
    def _expect(self,t,msg=""):
        if self._check(t): return self._advance()
        raise SyntaxError(f"[Parser] esperaba {t.name} pero encontré '{self.current.valor}'. {msg}")

    def parse_programa(self):
        p=NodoPrograma()
        while not self._end():
            p.sentencias.append(self._stmt())
            self._match(TipoToken.SEMICOL)
        return p

    def _stmt(self):
        if self._match(TipoToken.SELECT): return self._select()
        if self._match(TipoToken.INSERT): return self._insert()
        if self._match(TipoToken.DELETE): return self._delete()
        if self._match(TipoToken.CREATE): return self._create()
        raise SyntaxError(f"[Parser] sentencia desconocida: '{self.current.valor}'")

    def _select(self):
        # Permitir tanto * como nombre de columna
        if self._match(TipoToken.STAR):
            columna = "*"
        else:
            columna = self._expect(TipoToken.IDENT,"después de SELECT debe ir * o nombre de columna").valor
        
        self._expect(TipoToken.FROM,f"después de {columna} debe ir FROM")
        tabla=self._expect(TipoToken.IDENT,"nombre de tabla").valor

        # SELECT * FROM tabla  (sin WHERE, con LIMIT opcional)
        if not self._check(TipoToken.WHERE):
            limite = None
            if self._match(TipoToken.LIMIT):
                limite = int(self._expect(TipoToken.NUMBER,"número después de LIMIT").valor)
            return NodoSelectTodos(tabla=tabla, limite=limite)

        self._expect(TipoToken.WHERE,"debe ir WHERE")
        nodo = self._condicion(tabla, columna)
        # LIMIT opcional al final de cualquier SELECT
        if self._match(TipoToken.LIMIT):
            limite = int(self._expect(TipoToken.NUMBER,"número después de LIMIT").valor)
            if isinstance(nodo, NodoSelectTodos):
                nodo.limite = limite
        return nodo

    def _condicion(self, tabla, select_columna="*"):
        col=self._expect(TipoToken.IDENT,"nombre de columna").valor
        if self._match(TipoToken.EQ):
            return NodoSelectPuntual(tabla=tabla,columna=col,valor=self._valor())
        if self._match(TipoToken.BETWEEN):
            v1=self._valor()
            self._expect(TipoToken.AND,"falta AND en BETWEEN")
            v2=self._valor()
            return NodoSelectRango(tabla=tabla,columna=col,inicio=v1,fin=v2)
        if self._match(TipoToken.IN):
            self._expect(TipoToken.LPAREN,"falta ( después de IN")
            self._expect(TipoToken.POINT,"falta POINT")
            self._expect(TipoToken.LPAREN,"falta ( después de POINT")
            x=self._expect(TipoToken.NUMBER,"coordenada x").valor
            self._expect(TipoToken.COMMA,"falta ,")
            y=self._expect(TipoToken.NUMBER,"coordenada y").valor
            self._expect(TipoToken.RPAREN,"falta )")
            self._expect(TipoToken.COMMA,"falta ,")
            if self._match(TipoToken.RADIUS):
                r=self._expect(TipoToken.NUMBER,"radio").valor
                self._expect(TipoToken.RPAREN,"falta )")
                return NodoSelectRadio(tabla=tabla,columna=col,x=float(x),y=float(y),radio=float(r))
            if self._match(TipoToken.K):
                k=self._expect(TipoToken.NUMBER,"k").valor
                self._expect(TipoToken.RPAREN,"falta )")
                return NodoSelectKNN(tabla=tabla,columna=col,x=float(x),y=float(y),k=int(k))
            raise SyntaxError("[Parser] después de POINT se esperaba RADIUS o K")
        raise SyntaxError(f"[Parser] condición inválida: encontré '{self.current.valor}'")

    def _insert(self):
        self._expect(TipoToken.INTO,"falta INTO")
        tabla=self._expect(TipoToken.IDENT,"nombre de tabla").valor
        self._expect(TipoToken.VALUES,"falta VALUES")
        self._expect(TipoToken.LPAREN,"falta (")
        vals=self._lista_valores()
        self._expect(TipoToken.RPAREN,"falta )")
        return NodoInsert(tabla=tabla,valores=vals)

    def _delete(self):
        self._expect(TipoToken.FROM,"falta FROM")
        tabla=self._expect(TipoToken.IDENT,"nombre de tabla").valor
        self._expect(TipoToken.WHERE,"falta WHERE")
        col=self._expect(TipoToken.IDENT,"nombre de columna").valor
        self._expect(TipoToken.EQ,"falta =")
        return NodoDelete(tabla=tabla,columna=col,valor=self._valor())

    def _create(self):
        self._expect(TipoToken.TABLE,"falta TABLE")
        tabla=self._expect(TipoToken.IDENT,"nombre de tabla").valor
        self._expect(TipoToken.LPAREN,"falta (")
        cols=self._lista_columnas()
        self._expect(TipoToken.RPAREN,"falta )")
        archivo=None
        if self._match(TipoToken.FROM):
            self._expect(TipoToken.FILE,"falta FILE")
            archivo=self._expect(TipoToken.STRING,"path entre comillas").valor
        return NodoCreateTable(tabla=tabla,columnas=cols,archivo=archivo)

    def _lista_columnas(self):
        cols=[self._columna()]
        while self._match(TipoToken.COMMA): cols.append(self._columna())
        return cols

    def _columna(self):
        n=self._expect(TipoToken.IDENT,"nombre columna").valor
        t=self._expect(TipoToken.IDENT,"tipo columna").valor
        idx=None
        if self._match(TipoToken.INDEX):
            idx=self._expect(TipoToken.IDENT,"técnica de índice").valor.upper()
        return NodoColumna(nombre=n,tipo=t,indice=idx)

    def _lista_valores(self):
        v=[self._valor()]
        while self._match(TipoToken.COMMA): v.append(self._valor())
        return v

    def _valor(self):
        if self._match(TipoToken.NUMBER): return self.prev.valor
        if self._match(TipoToken.STRING): return self.prev.valor
        if self._match(TipoToken.IDENT):  return self.prev.valor
        raise SyntaxError(f"[Parser] valor esperado, encontré '{self.current.valor}'")

def parsear(sql:str)->NodoPrograma:
    return Parser(Scanner(sql).tokens).parse_programa()

# ── Tests ──────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    casos=[
        ("CREATE TABLE con 4 índices",
         'CREATE TABLE books (book_key INT INDEX BPTREE, title TEXT, author TEXT, pages INT, average_rating FLOAT, published_date INT) FROM FILE "books.csv";',
         False),
        ("CREATE TABLE Sequential File",
         'CREATE TABLE libros (book_key INT INDEX SEQUENTIAL, title TEXT) FROM FILE "libros.csv";',
         False),
        ("CREATE TABLE Hash",
         'CREATE TABLE libros (book_key INT INDEX HASH, title TEXT) FROM FILE "libros.csv";',
         False),
        ("SELECT puntual int",
         'SELECT * FROM books WHERE book_key = 6;', False),
        ("SELECT puntual string",
         'SELECT * FROM books WHERE title = "Soseki";', False),
        ("SELECT BETWEEN",
         'SELECT * FROM books WHERE book_key BETWEEN 1 AND 100;', False),
        ("SELECT RADIUS (RTree)",
         'SELECT * FROM books WHERE coords IN (POINT(12.5, -77.0), RADIUS 10);', False),
        ("SELECT KNN (RTree)",
         'SELECT * FROM books WHERE coords IN (POINT(12.5, -77.0), K 5);', False),
        ("INSERT completo",
         'INSERT INTO books VALUES (500, "enhypen yey", "mafer", 7, 5.0, 2020);', False),
        ("DELETE por clave",
         'DELETE FROM books WHERE book_key = 500;', False),
        ("Múltiples sentencias",
         'INSERT INTO books VALUES (1, "A", "B", 10, 4.0, 2020); SELECT * FROM books WHERE book_key = 1; DELETE FROM books WHERE book_key = 1;',
         False),
        ("ERROR falta * en SELECT",
         'SELECT FROM books WHERE book_key = 1;', True),
        ("ERROR falta AND en BETWEEN",
         'SELECT * FROM books WHERE book_key BETWEEN 1 100;', True),
        ("ERROR sentencia desconocida",
         'UPDATE books SET title = "X";', True),
    ]

    ok=fail=0
    for nombre, sql, debe_fallar in casos:
        print(f"\n{'='*62}")
        print(f"  {'[DEBE FALLAR] ' if debe_fallar else ''}{nombre}")
        print(f"  SQL: {sql[:75]}{'...' if len(sql)>75 else ''}")
        print(f"{'='*62}")
        try:
            res=parsear(sql)
            for s in res.sentencias:
                print(f"  AST → {s}")
            if debe_fallar:
                print("  ✗ debería haber fallado"); fail+=1
            else:
                print("  ✓ ok"); ok+=1
        except SyntaxError as e:
            if debe_fallar:
                print(f"  ✓ error esperado: {e}"); ok+=1
            else:
                print(f"  ✗ error inesperado: {e}"); fail+=1

    print(f"\n{'='*62}")
    print(f"  {ok} ok  |  {fail} fallidos")
    print(f"{'='*62}")