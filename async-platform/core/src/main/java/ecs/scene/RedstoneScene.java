package ecs.scene;

import ecs.ArchetypeWorld;
import ecs.ComponentRegistry;
import ecs.Components;
import ecs.GameSystem;
import ecs.systems.RedstonePropagate;

import java.util.List;

/**
 * Плотная редстоун-сетка WxH, источник в углу (index 0). Весь мир — архетип REDSTONE.
 * Сигнал распространяется от угла; глубина каскада ограничена дальностью сигнала (затухание 15→0).
 * gridWidth — топология сцены, а не компонент, поэтому живёт в системе, а не в мире.
 */
public final class RedstoneScene {

    public static final long REDSTONE = Components.mask(Components.POWER, Components.POWER_NEXT,
            Components.SOURCE);

    public static List<GameSystem> systems(int gridW) {
        return List.of(new RedstonePropagate(gridW));
    }

    public static ArchetypeWorld build(int gridW, int gridH) {
        int n = gridW * gridH;
        ComponentRegistry reg = Components.standard();
        ArchetypeWorld w = new ArchetypeWorld(reg, Math.max(1, n));
        for (int e = 0; e < n; e++) w.createEntity(REDSTONE);
        if (n > 0) w.storeOf(0).intCol(Components.SOURCE)[w.rowOf(0)] = 1; // источник в углу
        return w;
    }

    private RedstoneScene() {}
}
