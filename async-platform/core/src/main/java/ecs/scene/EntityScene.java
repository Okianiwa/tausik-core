package ecs.scene;

import ecs.ArchetypeStore;
import ecs.ArchetypeWorld;
import ecs.ComponentRegistry;
import ecs.Components;
import ecs.GameSystem;
import ecs.systems.MobCombat;
import ecs.systems.MobMove;
import ecs.systems.MobSense;

import java.util.List;

/**
 * Плотная стая мобов (грязная подсистема): весь мир — архетип MOB, сущности читают позиции
 * соседей. Инициализация детерминирована (хеш индекса, без RNG). Поле [0,1000)².
 * Все три системы матчат ОДИН архетип, поэтому конфликты решаются внутри него: r∩r по POSITION
 * не конфликт → [Sense,Combat] в одну стадию, writer POSITION (Move) — в отдельную.
 */
public final class EntityScene {

    public static final long MOB = Components.mask(Components.POSITION, Components.VELOCITY,
            Components.HEALTH);

    /** Стабильный порядок: Sense → Move → Combat. Раскладка: [Sense,Combat] | [Move]. */
    public static List<GameSystem> systems() {
        return List.of(new MobSense(), new MobMove(), new MobCombat());
    }

    public static ArchetypeWorld build(int n) {
        ComponentRegistry reg = Components.standard();
        ArchetypeWorld w = new ArchetypeWorld(reg, Math.max(1, n));
        for (int e = 0; e < n; e++) {
            int id = w.createEntity(MOB);
            ArchetypeStore s = w.storeOf(id);
            int r = w.rowOf(id);
            long h = e * 2654435761L + 0x9E3779B97F4A7C15L;
            s.doubleCol(Components.POSITION)[r * 2 + Components.LANE_X] = ((h >>> 11) % 100_000) / 100.0;
            s.doubleCol(Components.POSITION)[r * 2 + Components.LANE_Y] = ((h >>> 23) % 100_000) / 100.0;
            s.intCol(Components.HEALTH)[r] = 100;
        }
        return w;
    }

    private EntityScene() {}
}
