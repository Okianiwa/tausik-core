package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Полёт дропа: интеграция позиции по скорости, тор-обёртка в поле [0,FIELD).
 * Отдельная система от MobMove, а не переиспользование: MobMove матчит архетип MOB (у него HEALTH
 * есть, а SOURCE нет) и, будь она в этой сцене, матчила бы и дроп — раскладка сцены стала бы
 * зависеть от чужой системы. Дублирование трёх строк дешевле такой связи.
 */
public final class DropMove implements GameSystem {
    private static final double FIELD = 1000.0;

    public String name() { return "DropMove"; }
    public long reads()  { return Components.mask(Components.VELOCITY); }
    public long writes() { return Components.mask(Components.POSITION); }

    public void run(View v, int row, CommandBuffer cb) {
        double x = wrap(v.getDouble(Components.POSITION, row, Components.LANE_X)
                + v.getDouble(Components.VELOCITY, row, Components.LANE_X));
        double y = wrap(v.getDouble(Components.POSITION, row, Components.LANE_Y)
                + v.getDouble(Components.VELOCITY, row, Components.LANE_Y));
        v.setDouble(Components.POSITION, row, Components.LANE_X, x);
        v.setDouble(Components.POSITION, row, Components.LANE_Y, y);
        // busy() СОЗНАТЕЛЬНО не зовётся: архетип DROP не несёт BUSY, и при Work.WEIGHT>0 вызов упал
        // бы прямо в бенчмарке. Регулятор веса работы этой сцене и не нужен — её предмет измерения
        // структурная текучка, а не вес per-entity работы (его мерили срезы 3 и 6).
    }

    private static double wrap(double x) { return x - FIELD * Math.floor(x / FIELD); }
}
