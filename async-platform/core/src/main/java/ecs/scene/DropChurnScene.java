package ecs.scene;

import ecs.ArchetypeStore;
import ecs.ArchetypeWorld;
import ecs.ComponentRegistry;
import ecs.Components;
import ecs.GameSystem;
import ecs.systems.DropAge;
import ecs.systems.DropMove;
import ecs.systems.DropSpawn;

import java.util.List;

/**
 * СТРУКТУРНАЯ ТЕКУЧКА — сцена, которой у стенда не было ни одной: источники непрерывно роняют дроп,
 * дроп стареет и деспавнится. Спавн/деспавн предмета — самый частый структурный паттерн MC.
 *
 * Зачем отдельная сцена, а не «добавим спавн в существующую»: срезы 1-6 мерялись на ПРЯМЫХ записях
 * и отложенных эффектах на инвентарь, мир при этом был статичен — ничего не рождалось и не исчезало.
 * Числа speedup тех срезов к этому классу нагрузки НЕ ОТНОСЯТСЯ. Здесь каждый тик двигает строки
 * (swap-remove при деспавне) и растит карту (монотонные id при спавне) — это другая физика.
 *
 * Установившееся состояние: SPAWNERS источников × LIFETIME тиков жизни = столько дропов живёт
 * одновременно, при SPAWNERS спавнов и SPAWNERS деспавнов за тик.
 *
 * Раскладка — ОДНА стадия, и это проверяется тестом, а не подразумевается:
 *   DropSpawn r{POSITION,SOURCE} w{} | DropAge r{} w{HEALTH} | DropMove r{VELOCITY} w{POSITION}
 * Spawn×Move конфликтуют ПО КОМПОНЕНТАМ (POSITION), но матчат РАЗНЫЕ архетипы (у SPAWNER нет
 * VELOCITY, у DROP нет SOURCE) → conflict = компоненты И архетипы (decision #3) → конфликта нет.
 *
 * АРХЕТИП DROP НАМЕРЕННО НЕ СОЗДАЁТСЯ ПРИ СБОРКЕ: он рождается в фазе S первого тика. Значит сцена
 * проходит настоящий путь «новый архетип на лету» (ensurePlan пересчитает раскладку к тику 2), а не
 * тепличный. Тик 1 у неё поэтому дешевле остальных — для JMH это шум прогрева, steady state с тика 2.
 */
public final class DropChurnScene {

    /** Источник дропа: позиция + маркер SOURCE (он же не даёт DropSpawn матчить сам дроп). */
    public static final long SPAWNER = Components.mask(Components.POSITION, Components.SOURCE);

    /** Дроп: летит и стареет. HEALTH — остаток жизни в тиках. */
    public static final long DROP = Components.mask(Components.POSITION, Components.VELOCITY,
            Components.HEALTH);

    /** Сколько тиков живёт дроп до деспавна. */
    public static final int LIFETIME = 20;

    /** Стабильный порядок систем. Раскладка: одна стадия (см. разбор выше). */
    public static List<GameSystem> systems() {
        return List.of(new DropSpawn(), new DropAge(), new DropMove());
    }

    public static ArchetypeWorld build(int spawners) {
        ComponentRegistry reg = Components.standard();
        // Ёмкость с запасом на текучку: id монотонны и не переиспользуются (decision #7), поэтому
        // карта растёт по числу КОГДА-ЛИБО созданных. Это осознанная цена, её и мерит срез 7.
        ArchetypeWorld w = new ArchetypeWorld(reg, Math.max(1, spawners * (LIFETIME + 2)));
        for (int e = 0; e < spawners; e++) {
            int id = w.createEntity(SPAWNER);
            ArchetypeStore s = w.storeOf(id);
            int r = w.rowOf(id);
            long h = e * 2654435761L + 0x9E3779B97F4A7C15L; // без RNG: два прогона = один мир
            s.doubleCol(Components.POSITION)[r * 2 + Components.LANE_X] = ((h >>> 11) % 100_000) / 100.0;
            s.doubleCol(Components.POSITION)[r * 2 + Components.LANE_Y] = ((h >>> 23) % 100_000) / 100.0;
            s.intCol(Components.SOURCE)[r] = 1;
        }
        return w;
    }

    private DropChurnScene() {}
}
