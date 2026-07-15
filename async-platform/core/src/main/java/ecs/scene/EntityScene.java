package ecs.scene;

import ecs.Archetype;
import ecs.GameSystem;
import ecs.World;
import ecs.systems.MobCombat;
import ecs.systems.MobMove;
import ecs.systems.MobSense;

import java.util.List;

/**
 * Плотная стая мобов (грязная подсистема): весь мир — архетип MOB, сущности читают позиции
 * соседей. Инициализация детерминирована (хеш индекса, без RNG). Поле [0,1000)².
 */
public final class EntityScene {

    /** Стабильный порядок: Sense → Move → Combat. Раскладка: [Sense,Combat] | [Move]. */
    public static List<GameSystem> systems() {
        return List.of(new MobSense(), new MobMove(), new MobCombat());
    }

    public static World build(int n) {
        World w = new World(n);
        w.archLo[Archetype.MOB] = 0;
        w.archHi[Archetype.MOB] = n;
        for (int e = 0; e < n; e++) {
            long h = e * 2654435761L + 0x9E3779B97F4A7C15L;
            w.posX[e] = ((h >>> 11) % 100_000) / 100.0; // [0,1000)
            w.posY[e] = ((h >>> 23) % 100_000) / 100.0;
            w.health[e] = 100;
        }
        return w;
    }

    private EntityScene() {}
}
