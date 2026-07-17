"""Resolución local de IP a coordenadas sin enviar telemetría a terceros."""

from __future__ import annotations

import csv
import ipaddress
import sqlite3
from pathlib import Path
from typing import Any, Iterable


def ip_number(value: str) -> int:
    return int(ipaddress.ip_address(value))


class GeoDatabase:
    """Índice SQLite reproducible a partir del catálogo IPv4 corporativo."""
    def __init__(self, csv_path: Path, db_path: Path):
        self.csv_path = csv_path
        self.db_path = db_path

    def available(self) -> bool:
        return self.db_path.exists()

    def build(self) -> int:
        if not self.csv_path.exists():
            raise FileNotFoundError(self.csv_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.execute("DROP TABLE IF EXISTS ipv4")
        connection.execute("CREATE TABLE ipv4 (start INTEGER NOT NULL, end INTEGER NOT NULL, country TEXT, city TEXT, latitude REAL, longitude REAL)")
        count = 0
        with self.csv_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            batch = []
            for row in reader:
                batch.append((ip_number(row["start_ip"]), ip_number(row["end_ip"]), row["country"], row["city"], float(row["latitude"]), float(row["longitude"])))
                if len(batch) == 10000:
                    connection.executemany("INSERT INTO ipv4 VALUES (?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
                count += 1
            if batch:
                connection.executemany("INSERT INTO ipv4 VALUES (?, ?, ?, ?, ?, ?)", batch)
        connection.execute("CREATE INDEX idx_ipv4_start ON ipv4(start)")
        connection.commit()
        connection.close()
        return count

    def locate(self, ips: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
        """Geolocaliza IP públicas y agrupa las privadas en la sede acordada."""
        if not self.available():
            return []
        connection = sqlite3.connect(self.db_path)
        result = []
        local_count = 0
        for ip, count in ips:
            try:
                address = ipaddress.ip_address(ip)
                if not address.is_global:
                    local_count += count
                    continue
                number = int(address)
            except ValueError:
                continue
            row = connection.execute(
                "SELECT country, city, latitude, longitude FROM ipv4 WHERE start <= ? AND end >= ? ORDER BY start DESC LIMIT 1",
                (number, number),
            ).fetchone()
            if row:
                result.append({"ip": ip, "count": count, "country": row[0], "city": row[1], "lat": row[2], "lng": row[3]})
        connection.close()
        if local_count:
            result.append({"ip": "Red local", "count": local_count, "country": "España", "city": "Calle Embajadores 181, Madrid", "lat": 40.3912, "lng": -3.69233, "local": True})
        return result
