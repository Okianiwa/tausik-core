package ecs.scene;

import ecs.Components;
import ecs.GameSystem;
import ecs.World;
import ecs.systems.FurnaceSmelt;
import ecs.systems.HopperTransfer;
import ecs.systems.MachineProcess;

import java.util.List;

/**
 * Плотная сцена «мегафабрики»: тысячи блок-энтити в паре чанков. Архетипы гейтятся ДАННЫМИ
 * (печь: recipeTicks>0; машина: energy>0; хоппер: link>=0), поэтому системы бегут по всему
 * массиву, но каждая работает только над своими строками. Инициализация детерминирована.
 */
public final class BlockEntityScene {
    public static final int RECIPE_TICKS = 8;
    public static final double FURNACE_HEAT = 500.0;

    /** Стабильный порядок систем — единственный источник недетерминизма, зафиксирован. */
    public static List<GameSystem> systems() {
        return List.of(new FurnaceSmelt(), new MachineProcess(), new HopperTransfer());
    }

    public static World build(int n) {
        World w = new World(n);
        int third = Math.max(1, n / 3);
        int furnaces = Math.min(third, n);
        int machines = Math.min(third, Math.max(0, n - furnaces));

        // Disjoint-диапазоны строк по архетипу — основа тонкой гранулярности конфликта.
        w.archLo[ecs.Archetype.FURNACE] = 0;             w.archHi[ecs.Archetype.FURNACE] = furnaces;
        w.archLo[ecs.Archetype.MACHINE] = furnaces;      w.archHi[ecs.Archetype.MACHINE] = furnaces + machines;
        w.archLo[ecs.Archetype.HOPPER]  = furnaces + machines; w.archHi[ecs.Archetype.HOPPER] = n;

        for (int e = 0; e < n; e++) {
            if (e < furnaces) {                       // печь
                w.recipeTicks[e] = RECIPE_TICKS;
                w.heat[e] = FURNACE_HEAT;
                w.burnTime[e] = 0;
                w.inv[w.invIndex(e, Components.SLOT_FUEL)]  = 100_000;
                w.inv[w.invIndex(e, Components.SLOT_INPUT)] = 100_000;
                w.link[e] = -1;
            } else if (e < furnaces + machines) {     // машина
                w.energy[e] = 100_000_000L;
                w.inv[w.invIndex(e, Components.SLOT_INPUT)] = 100_000;
                w.link[e] = -1;
            } else {                                  // хоппер → печь (i % furnaces)
                w.link[e] = e % furnaces;
                w.inv[w.invIndex(e, Components.SLOT_OUTPUT)] = 100_000;
            }
        }
        return w;
    }

    private BlockEntityScene() {}
}
