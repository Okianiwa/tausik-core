package ecs.scene;

import ecs.ArchetypeStore;
import ecs.ArchetypeWorld;
import ecs.ComponentRegistry;
import ecs.Components;
import ecs.GameSystem;
import ecs.systems.FurnaceSmelt;
import ecs.systems.HopperTransfer;
import ecs.systems.MachineProcess;

import java.util.List;

/**
 * Плотная сцена «мегафабрики»: тысячи блок-энтити в паре чанков. Инициализация детерминирована.
 *
 * Архетипы теперь — НАСТОЯЩИЕ наборы компонентов, а не гейтинг данными по общим колонкам.
 * Это принципиально: печь и машина обе пишут INVENTORY, и если бы у них был одинаковый набор
 * компонентов, они смэтчили бы ОДИН архетип → конфликт → раскладка откатилась бы с 1 стадии на 3
 * (потеря среза 2). Различает их именно набор: у печи RECIPE/HEAT/FUEL, у машины ENERGY.
 * BUSY входит в маски блок-энтити, потому что эти системы зовут v.busy() (регулятор веса, срез 3).
 */
public final class BlockEntityScene {
    public static final int RECIPE_TICKS = 8;
    public static final double FURNACE_HEAT = 500.0;

    public static final long FURNACE = Components.mask(Components.RECIPE, Components.HEAT,
            Components.FUEL, Components.INVENTORY, Components.PROGRESS, Components.BUSY);
    public static final long MACHINE = Components.mask(Components.ENERGY, Components.INVENTORY,
            Components.PROGRESS, Components.BUSY);
    public static final long HOPPER = Components.mask(Components.INVENTORY, Components.LINK,
            Components.BUSY);

    /** Стабильный порядок систем — единственный источник недетерминизма, зафиксирован. */
    public static List<GameSystem> systems() {
        return List.of(new FurnaceSmelt(), new MachineProcess(), new HopperTransfer());
    }

    public static ArchetypeWorld build(int n) {
        ComponentRegistry reg = Components.standard();
        ArchetypeWorld w = new ArchetypeWorld(reg, Math.max(1, n));
        int third = Math.max(1, n / 3);
        int furnaces = Math.min(third, n);
        int machines = Math.min(third, Math.max(0, n - furnaces));

        // Печи создаются первыми → их entityId = 0..furnaces-1, поэтому LINK хоппера (e % furnaces)
        // остаётся валидным entityId печи, как и в прежней раскладке.
        for (int e = 0; e < n; e++) {
            if (e < furnaces) {
                int id = w.createEntity(FURNACE);
                ArchetypeStore s = w.storeOf(id);
                int r = w.rowOf(id);
                s.intCol(Components.RECIPE)[r] = RECIPE_TICKS;
                s.doubleCol(Components.HEAT)[r] = FURNACE_HEAT;
                s.intCol(Components.FUEL)[r] = 0;
                s.intCol(Components.INVENTORY)[r * Components.SLOTS + Components.SLOT_FUEL] = 100_000;
                s.intCol(Components.INVENTORY)[r * Components.SLOTS + Components.SLOT_INPUT] = 100_000;
            } else if (e < furnaces + machines) {
                int id = w.createEntity(MACHINE);
                ArchetypeStore s = w.storeOf(id);
                int r = w.rowOf(id);
                s.longCol(Components.ENERGY)[r] = 100_000_000L;
                s.intCol(Components.INVENTORY)[r * Components.SLOTS + Components.SLOT_INPUT] = 100_000;
            } else {
                int id = w.createEntity(HOPPER);
                ArchetypeStore s = w.storeOf(id);
                int r = w.rowOf(id);
                s.intCol(Components.LINK)[r] = e % furnaces; // цель — стабильный entityId печи
                s.intCol(Components.INVENTORY)[r * Components.SLOTS + Components.SLOT_OUTPUT] = 100_000;
            }
        }
        return w;
    }

    private BlockEntityScene() {}
}
