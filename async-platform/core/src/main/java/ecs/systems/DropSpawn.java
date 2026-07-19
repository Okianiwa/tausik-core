package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;
import ecs.scene.DropChurnScene;

/**
 * Источник дропа: каждый тик роняет один предмет со своей позиции. Спавн предмета — самый частый
 * структурный паттерн MC (сломал блок, убил моба, вытряхнул сундук).
 *
 * Эмитит create() и получает ДЕСКРИПТОР, а не entityId: реального id ещё нет, он выдаётся в фазе S
 * барьера по позиции в тотальном порядке. Начальные значения едут через init() в СТРУКТУРНОМ потоке —
 * обычным set() их не задать, в фазе эффектов новорождённой не существует.
 *
 * Скорость — хеш от позиции источника и его строки, без RNG: два прогона обязаны дать один мир.
 */
public final class DropSpawn implements GameSystem {

    public String name() { return "DropSpawn"; }
    public long reads()  { return Components.mask(Components.POSITION, Components.SOURCE); }
    public long writes() { return 0; }

    public void run(View v, int row, CommandBuffer cb) {
        double x = v.getDouble(Components.POSITION, row, Components.LANE_X);
        double y = v.getDouble(Components.POSITION, row, Components.LANE_Y);

        // Ключ детерминирован СТАБИЛЬНЫМ entityId источника, а не localRow: строки двигаются
        // (swap-remove), и привязка к строке дала бы расхождение ref vs par при том же состоянии.
        long h = v.entityAt(row) * 2654435761L + 0x9E3779B97F4A7C15L;

        int d = cb.create(DropChurnScene.DROP);
        cb.initDouble(d, Components.POSITION, Components.LANE_X, x);
        cb.initDouble(d, Components.POSITION, Components.LANE_Y, y);
        cb.initDouble(d, Components.VELOCITY, Components.LANE_X, ((h >>> 11) % 200 - 100) / 100.0);
        cb.initDouble(d, Components.VELOCITY, Components.LANE_Y, ((h >>> 23) % 200 - 100) / 100.0);
        cb.init(d, Components.HEALTH, 0, DropChurnScene.LIFETIME);
    }
}
