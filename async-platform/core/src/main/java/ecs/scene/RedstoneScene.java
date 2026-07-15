package ecs.scene;

import ecs.Archetype;
import ecs.GameSystem;
import ecs.World;
import ecs.systems.RedstonePropagate;

import java.util.List;

/**
 * Плотная редстоун-сетка WxH, источник в углу (index 0). Весь мир — архетип REDSTONE.
 * Сигнал распространяется от угла; глубина каскада (число проходов) ≈ манхэттенский диаметр.
 */
public final class RedstoneScene {

    public static List<GameSystem> systems() {
        return List.of(new RedstonePropagate());
    }

    public static World build(int gridW, int gridH) {
        int n = gridW * gridH;
        World w = new World(n);
        w.gridWidth = gridW;
        w.archLo[Archetype.REDSTONE] = 0;
        w.archHi[Archetype.REDSTONE] = n;
        if (n > 0) w.source[0] = 1; // источник в углу
        return w;
    }

    private RedstoneScene() {}
}
