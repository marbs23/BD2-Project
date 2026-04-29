import struct
from typing import NamedTuple


class TID(NamedTuple):
    page_id: int
    slot_id: int


class MBR:
    __slots__ = ("min_lon", "max_lon", "min_lat", "max_lat")

    _PACK_FMT = "<4d"
    _PACK_SIZE = struct.calcsize(_PACK_FMT)

    def __init__(self, min_lon: float, max_lon: float, min_lat: float, max_lat: float):
        self.min_lon = min_lon
        self.max_lon = max_lon
        self.min_lat = min_lat
        self.max_lat = max_lat

    @classmethod
    def from_point(cls, lon: float, lat: float) -> "MBR":
        return cls(lon, lon, lat, lat)

    def expand(self, other: "MBR") -> "MBR":
        return MBR(
            min(self.min_lon, other.min_lon),
            max(self.max_lon, other.max_lon),
            min(self.min_lat, other.min_lat),
            max(self.max_lat, other.max_lat),
        )

    def area(self) -> float:
        return (self.max_lon - self.min_lon) * (self.max_lat - self.min_lat)

    def area_enlargement(self, other: "MBR") -> float:
        return self.expand(other).area() - self.area()

    def intersects_mbr(self, other: "MBR") -> bool:
        return (
            self.min_lon <= other.max_lon
            and self.max_lon >= other.min_lon
            and self.min_lat <= other.max_lat
            and self.max_lat >= other.min_lat
        )

    def contains_point(self, lon: float, lat: float) -> bool:
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat

    def mindist_sq(self, px: float, py: float) -> float:
        dx = max(self.min_lon - px, 0.0, px - self.max_lon)
        dy = max(self.min_lat - py, 0.0, py - self.max_lat)
        return dx * dx + dy * dy

    def intersects_circle(self, cx: float, cy: float, r: float) -> bool:
        return self.mindist_sq(cx, cy) <= r * r

    def to_tuple(self) -> tuple:
        return (self.min_lon, self.max_lon, self.min_lat, self.max_lat)

    def pack_into(self, buf: bytearray, offset: int) -> None:
        struct.pack_into(self._PACK_FMT, buf, offset,
                         self.min_lon, self.max_lon, self.min_lat, self.max_lat)

    @classmethod
    def unpack_from(cls, buf, offset: int) -> "MBR":
        min_lon, max_lon, min_lat, max_lat = struct.unpack_from(cls._PACK_FMT, buf, offset)
        return cls(min_lon, max_lon, min_lat, max_lat)

    def __repr__(self) -> str:
        return (f"MBR(lon=[{self.min_lon:.4f},{self.max_lon:.4f}], "
                f"lat=[{self.min_lat:.4f},{self.max_lat:.4f}])")

    def __eq__(self, other) -> bool:
        if not isinstance(other, MBR):
            return False
        return (self.min_lon == other.min_lon and self.max_lon == other.max_lon
                and self.min_lat == other.min_lat and self.max_lat == other.max_lat)
