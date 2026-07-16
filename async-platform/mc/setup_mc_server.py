"""Приводит машину к «сервер Minecraft 26.2 запускается, код читается».

Предусловие всего остального на стороне майна (задача mc-26-2-setup).

ЛИЦЕНЗИЯ. Деобфусцировано != открыто. Mojang сохраняет собственность на исходники;
скрипт качает артефакт ЛОКАЛЬНО и ничего не коммитит — за этим следит .gitignore рядом.
EULA принимается только явным --accept-eula: принимать чужое лицензионное соглашение
молча, за пользователя, скрипт не вправе.

ИСТОЧНИК только официальный: version_manifest_v2 -> version json -> downloads.server.
URL не хардкодится (он меняется от версии к версии), а выводится из манифеста; sha1
берётся оттуда же и ПРОВЕРЯЕТСЯ — несовпадение означает отказ, а не предупреждение.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
DEFAULT_VERSION = "26.2"
HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE / "server"

# Требование Mojang для 26.2 (javaVersion.majorVersion в version json). Java 24 не
# запустит классы, скомпилированные под 25 — это не «рекомендация», а верхняя граница
# class file version.
REQUIRED_JAVA = 25


def _get(url: str) -> bytes:
    # User-Agent обязателен: api.adoptium.net отдаёт 403 на дефолтный UA urllib.
    req = urllib.request.Request(url, headers={"User-Agent": "tausik-mc-setup/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(version: str) -> dict:
    """version_manifest -> version json -> метаданные server-артефакта."""
    manifest = json.loads(_get(MANIFEST_URL))
    entry = next((v for v in manifest["versions"] if v["id"] == version), None)
    if entry is None:
        ids = [v["id"] for v in manifest["versions"][:5]]
        raise SystemExit(f"версия {version!r} не найдена в манифесте; последние: {ids}")

    raw = _get(entry["url"])
    # Манифест публикует sha1 самого version json — проверяем и его, иначе доверие
    # к download-sha1 ниже держится на честном слове транспорта.
    got = hashlib.sha1(raw).hexdigest()
    if got != entry["sha1"]:
        raise SystemExit(f"sha1 version json не сошёлся: ждали {entry['sha1']}, получили {got}")

    vjson = json.loads(raw)
    server = vjson["downloads"]["server"]
    return {
        "id": vjson["id"],
        "release_time": entry["releaseTime"],
        "url": server["url"],
        "sha1": server["sha1"],
        "size": server["size"],
        "java_major": vjson.get("javaVersion", {}).get("majorVersion"),
        "has_mappings": [k for k in vjson["downloads"] if k.endswith("_mappings")],
    }


def download(meta: dict, *, force: bool = False) -> Path:
    """Идемпотентно: если jar на месте и sha1 сошёлся — не перекачиваем."""
    SERVER_DIR.mkdir(parents=True, exist_ok=True)
    jar = SERVER_DIR / f"minecraft_server.{meta['id']}.jar"

    if jar.exists() and not force:
        if _sha1(jar) == meta["sha1"]:
            print(f"[skip] {jar.name} уже на месте, sha1 сошёлся")
            return jar
        print(f"[warn] {jar.name} есть, но sha1 НЕ сошёлся — перекачиваю")

    tmp = jar.with_suffix(".jar.part")
    print(f"[get ] {meta['url']}")
    tmp.write_bytes(_get(meta["url"]))

    got = _sha1(tmp)
    if got != meta["sha1"]:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"ОТКАЗ: sha1 не сошёлся.\n  ждали:    {meta['sha1']}\n  получили: {got}\n"
            "Артефакт удалён. Ставить непроверенный jar скрипт не будет."
        )
    tmp.replace(jar)
    print(f"[ok  ] {jar.name}: {meta['size']:,} байт, sha1 сошёлся")
    return jar


def check_readable(jar: Path, samples: int = 4) -> bool:
    """Посылка эпика: 26.x необфусцирован. Проверяем ФАКТОМ, а не анонсом.

    ВНИМАНИЕ, ЛОВУШКА (наступил при первом прогоне): внешний jar — это BUNDLER,
    в нём всего 4 класса net/minecraft/bundler/Main*. Они читаемы ВСЕГДА, даже когда
    сервер обфусцирован, потому что это загрузчик, а не игра. Проверка по внешнему
    jar-у даёт ложное «читаемо». Настоящий сервер лежит вложенным:
    META-INF/versions/<ver>/server-<ver>.jar — смотреть надо ТУДА.
    """
    with zipfile.ZipFile(jar) as z:
        nested = [
            n for n in z.namelist() if n.startswith("META-INF/versions/") and n.endswith(".jar")
        ]
        if not nested:
            raise SystemExit(
                "в jar нет META-INF/versions/*.jar — раскладка изменилась, проверка "
                "читаемости смотрела бы на bundler и врала бы. Разобраться до выводов."
            )
        payload = zipfile.ZipFile(io.BytesIO(z.read(nested[0])))

    classes = [n for n in payload.namelist() if n.endswith(".class")]
    mc = [c for c in classes if c.startswith("net/minecraft/")]
    obf = [c for c in classes if len(Path(c).stem) <= 2]

    print(f"[read] вложенный сервер: {nested[0]}")
    print(f"[read] классов: {len(classes):,}, из них net/minecraft/*: {len(mc):,}")
    print(f"[read] имён <=2 символов (признак обфускации): {len(obf)}")
    for c in [c for c in mc if "MinecraftServer.class" in c or "/block/" in c][:samples]:
        print(f"         {c}")
    # Обфусцированный сервер = тысячи однобуквенных классов. Ноль таких при
    # тысячах net/minecraft/* — деобфускация, а не удачная выборка.
    return len(mc) > 1000 and len(obf) == 0


def provision_java() -> Path:
    """Достать JDK 25 локально: на машине его может не быть (у нас были 17/21/24).

    Temurin, а не рантайм Mojang (java-runtime-epsilon): у Mojang это дерево из сотен
    файлов под их лаунчер, у Adoptium — один архив с честным API. Ставим в jre/ рядом,
    он под .gitignore — это ЧУЖОЙ бинарник, в репозиторий ему нельзя так же, как коду
    Mojang.
    """
    jre_dir = HERE / "jre"
    existing = list(jre_dir.glob("jdk-25*/bin/java.exe")) + list(jre_dir.glob("jdk-25*/bin/java"))
    if existing:
        print(f"[java] уже стоит: {existing[0]}")
        return existing[0]

    api = (
        f"https://api.adoptium.net/v3/binary/latest/{REQUIRED_JAVA}/ga/"
        "windows/x64/jdk/hotspot/normal/eclipse"
    )
    jre_dir.mkdir(parents=True, exist_ok=True)
    archive = jre_dir / f"temurin-{REQUIRED_JAVA}.zip"
    print(f"[java] качаю JDK {REQUIRED_JAVA}: {api}")
    archive.write_bytes(_get(api))
    with zipfile.ZipFile(archive) as z:
        z.extractall(jre_dir)
    archive.unlink(missing_ok=True)

    found = list(jre_dir.glob("jdk-25*/bin/java.exe")) + list(jre_dir.glob("jdk-25*/bin/java"))
    if not found:
        raise SystemExit(f"JDK распакован в {jre_dir}, но java не найдена — смотреть руками")
    print(f"[java] поставлен: {found[0]}")
    return found[0]


def java_ok(java: str) -> tuple[bool, str]:
    try:
        out = subprocess.run(
            [java, "-version"], capture_output=True, text=True, encoding="utf-8", timeout=30
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"не запускается: {e}"
    text = (out.stderr or "") + (out.stdout or "")
    first = text.strip().splitlines()[0] if text.strip() else "?"
    for tok in first.replace('"', " ").split():
        head = tok.split(".")[0]
        if head.isdigit():
            major = int(head)
            return major >= REQUIRED_JAVA, f"{first} (major={major}, нужно >= {REQUIRED_JAVA})"
    return False, f"версию не разобрал: {first!r}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", default=DEFAULT_VERSION)
    p.add_argument("--java", default="java", help="путь к java (нужна >= 25)")
    p.add_argument("--force", action="store_true", help="перекачать даже если sha1 сошёлся")
    p.add_argument(
        "--accept-eula",
        action="store_true",
        help="записать eula.txt=true. Это ПРИНЯТИЕ лицензии Mojang — только осознанно.",
    )
    p.add_argument(
        "--check-only", action="store_true", help="только resolve + проверки, без скачивания"
    )
    p.add_argument(
        "--provision-java",
        action="store_true",
        help=f"скачать Temurin JDK {REQUIRED_JAVA} в jre/, если системная не годится",
    )
    a = p.parse_args()

    meta = resolve(a.version)
    print(f"[meta] {meta['id']} ({meta['release_time'][:10]}), java>={meta['java_major']}")
    if meta["has_mappings"]:
        print(f"[meta] маппинги публикуются: {meta['has_mappings']} -> версия ОБФУСЦИРОВАНА")
    else:
        print("[meta] маппингов в downloads НЕТ -> отображать нечего, версия деобфусцирована")
    if a.check_only:
        return 0

    jar = download(meta, force=a.force)
    check_readable(jar)

    java = a.java
    ok, why = java_ok(java)
    print(f"[java] {why} -> {'годится' if ok else 'НЕ ГОДИТСЯ'}")
    if not ok and a.provision_java:
        java = str(provision_java())
        ok, why = java_ok(java)
        print(f"[java] {why} -> {'годится' if ok else 'НЕ ГОДИТСЯ'}")

    if a.accept_eula:
        (SERVER_DIR / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        print("[eula] eula.txt=true записан по явному --accept-eula")
    else:
        print("[eula] НЕ принят. Сервер не стартует без него; передайте --accept-eula осознанно.")

    print(f"\nЗапуск: {java} -Xmx4G -jar {jar.name} nogui   (из {SERVER_DIR})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
