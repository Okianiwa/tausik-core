"""Тесты гардов стенда A/B. Каждый закрывает ошибку, которая УЖЕ случилась.

Стенд сам по себе — инструмент измерения, и его дефекты дают не падение, а
ПРАВДОПОДОБНОЕ НЕВЕРНОЕ ЧИСЛО. Поэтому его гарды проверяются тестами, а не
доверием: сцена, съеденная грунтом из-за отказа /fill, выглядела как рабочая.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ab_bench as ab

FILL_LIMIT = 32768  # предел /fill в 26.2: сервер отвечает «Too many blocks» и НЕ делает ничего


def _fill_volume(cmd: str) -> int:
    """Объём куба из команды `fill x1 y1 z1 x2 y2 z2 block`."""
    p = cmd.split()
    assert p[0] == "fill", cmd
    x1, y1, z1, x2, y2, z2 = (int(v) for v in p[1:7])
    return (abs(x2 - x1) + 1) * (abs(y2 - y1) + 1) * (abs(z2 - z1) + 1)


@pytest.mark.parametrize("side", [20, 40, 80, 160])
def test_fill_never_exceeds_server_limit(side: int) -> None:
    """ОДИННАДЦАТЫЙ случай тихой ошибки эпика: куб 82x82x5 = 33620 > 32768.

    Сервер отвечал «Too many blocks in the specified area» и НЕ ДЕЛАЛ НИЧЕГО —
    зомби спавнились в грунт и задыхались, сцена таяла (6400 -> 6377), а патч
    падал на структурных мутациях от их смертей. Ни одно из трёх следствий не
    указывало на /fill.
    """
    over = [
        c for c in ab.scene_setup(side) if c.startswith("fill") and _fill_volume(c) > FILL_LIMIT
    ]
    assert not over, f"fill превышает предел {FILL_LIMIT}: {over}"


def test_gamerules_use_262_names() -> None:
    """ДЕСЯТЫЙ случай (memory #56): имена правил сменились в 26.2.

    Старые doMobSpawning/randomTickSpeed дают `Incorrect argument`, а прогон идёт
    дальше и врёт. Здесь фиксируем ИМЕНА, которые сервер реально принимает.
    """
    assert set(ab.GAMERULES) == {
        "spawn_mobs",
        "advance_time",
        "advance_weather",
        "random_tick_speed",
        "max_entity_cramming",
        "entity_drops",
    }
    for old in ("doMobSpawning", "randomTickSpeed", "doDaylightCycle", "doWeatherCycle"):
        assert old not in ab.GAMERULES, f"старое имя правила 26.1 просочилось: {old}"


def test_cmd_fail_catches_silent_refusals() -> None:
    """Гард обязан ловить отказы, которые сервер печатает ВМЕСТО падения."""
    for line in (
        "[12:00:00] [Server thread/INFO]: Too many blocks in the specified area (maximum 32768, specified 33620)",
        "[12:00:00] [Server thread/INFO]: Incorrect argument for command",
        "[12:00:00] [Server thread/INFO]: Unknown or incomplete command",
        "[12:00:00] [Server thread/INFO]: Unable to summon entity",
    ):
        assert ab.RE_CMD_FAIL.search(line), f"НЕ пойман отказ: {line}"


def test_cmd_fail_ignores_normal_kill_response() -> None:
    """«No entity was found» — ШТАТНЫЙ ответ kill по пустой сцене, а не отказ.

    Гард, кричащий на норму, быстро научит себя игнорировать — и тогда он не
    поймает настоящий отказ.
    """
    assert not ab.RE_CMD_FAIL.search("[12:00:00] [Server thread/INFO]: No entity was found")


@pytest.mark.parametrize(
    ("side", "step", "want"),
    [(80, 1, 6400), (40, 1, 1600), (160, 2, 6400), (20, 1, 400)],
)
def test_mob_count_matches_generated_commands(side: int, step: int, want: int) -> None:
    """Счётчик ожидания и генератор команд обязаны сходиться.

    Расхождение здесь означало бы, что гард «сцена не собралась» сравнивает
    факт с неверным ожиданием и роняет ГОДНЫЕ прогоны (или пропускает битые).
    """
    assert ab.mob_count(side, step) == want
    assert len(ab.scene_mobs(side, step)) == want


def test_scene_setup_kills_leftovers_before_building() -> None:
    """Остатки прошлых стендов (предметы) деспавнятся ПО ХОДУ замера.

    Каждый деспавн — структурная мутация трекинга, то есть шум, не относящийся
    к мобам. Сцена обязана содержать только то, что мы меряем.
    """
    setup = ab.scene_setup(40)
    assert any(c in setup for c in ab.purge_cmds())


def test_purge_uses_negation_not_type_list() -> None:
    """Уборка ОБЯЗАНА бить отрицанием: список типов не покрывает то, чего в нём нет.

    Прежняя уборка знала про zombie/item/experience_orb. 339 zombie_villager,
    296 slime и 109 drowned от смешанного теста пережили её и тикали со сценой:
    ядро оттикало 7170 при сцене 6400, ваниль показала 126.3 мс вместо 90.5.
    Брак в 28% выглядел как факт — дрейф 0.0%, ошибок 0, маркер на месте.
    """
    cmds = ab.purge_cmds()
    assert any("type=!player" in c for c in cmds), cmds
    # Тип моба сцены нельзя вычищать перечислением: сцену с --mob villager
    # уборка по списку `zombie` не тронула бы вовсе.
    assert not any(f"type={ab.MOB[0]}]" in c for c in cmds), cmds


def test_teardown_purges_as_thoroughly_as_setup() -> None:
    """Teardown обязан чистить ровно так же, как setup.

    Дыра была именно здесь: setup знал четыре типа, teardown — два. Мир между
    прогонами СОХРАНЯЕТСЯ, поэтому недобитое teardown'ом тикает на старте
    следующей руки, до того как применятся правила сцены.
    """
    src = (Path(__file__).resolve().parents[1] / "ab_bench.py").read_text(encoding="utf-8")
    teardown = src.split("finally:")[1].split("srv.stop()")[0]
    assert "purge_cmds()" in teardown, "teardown обязан звать общую уборку, а не свой список типов"
    assert "type=item" not in teardown, "перечисление типов в teardown — это и есть дефект"


@pytest.mark.parametrize(
    "line",
    [
        "[14:38:51] [Yggdrasil Key Fetcher/ERROR]: Failed to request yggdrasil public key",
        "com.mojang.authlib.exceptions.MinecraftClientException: Failed to read from "
        "https://api.minecraftservices.com/publickeys due to Read timed out",
        "Caused by: java.net.SocketTimeoutException: Read timed out",
    ],
)
def test_env_noise_does_not_fail_the_run(line: str) -> None:
    """Таймаут внешнего API Mojang — шум среды, а не отказ ядра.

    Эти три строки браковали ЦЕЛЫЕ точки лестницы (19881/21904/24025), хотя
    падают в потоке Yggdrasil на старте и на тик не влияют: забракованные ими
    числа легли на модель, построенную по чистым прогонам, с ошибкой <3%.
    """
    assert not ab.is_real_error(line)


@pytest.mark.parametrize(
    "line",
    [
        "[14:38:51] [Server thread/ERROR]: Encountered an unexpected exception",
        "java.lang.NullPointerException: Cannot invoke Entity.tick()",
        "[Server thread/ERROR]: ConcurrentModificationException in EntityTickList",
    ],
)
def test_real_errors_still_fail_the_run(line: str) -> None:
    """Гард обязан ловить настоящие отказы — иначе смягчение выше стоило бы всего.

    Гонки в параллельном тике проявляются именно так: ConcurrentModification,
    NPE из воркера. Пропустить их = вернуть эпику самый дорогой класс ошибок.
    """
    assert ab.is_real_error(line)


def test_zone_selector_covers_whole_arena() -> None:
    """Селектор счёта обязан покрывать арену целиком, включая стены.

    Иначе «пропавшие» мобы окажутся просто за границей окна счёта, и гард
    горизонта соврёт про дрейф сцены.
    """
    side = 80
    sel = ab.zone_selector(side)
    assert f"dx={side + 2}" in sel and f"dz={side + 2}" in sel
    assert f"x={ab.X0 - 1}" in sel and f"z={ab.Z0 - 1}" in sel
