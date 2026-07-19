package ecs;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Контейнер архетипов: карта entityId ↔ (архетип, строка), миграция, layout-independent checksum.
 * Ключевой тест здесь — checksumIsLayoutIndependent (AC #3): он был НЕВЫРАЗИМ в фикс-колонках,
 * где индекс строки и был идентичностью энтити.
 */
class ArchetypeWorldTest {

    private static final long MOBISH = Components.mask(Components.HEALTH, Components.POSITION);
    private static final long MOBISH_PLUS = Components.mask(
            Components.HEALTH, Components.POSITION, Components.ENERGY);

    /** Три энтити с детерминированными значениями в архетипе MOBISH. */
    private static ArchetypeWorld build() {
        ArchetypeWorld w = new ArchetypeWorld(Components.standard(), 4);
        for (int i = 0; i < 3; i++) {
            int id = w.createEntity(MOBISH);
            ArchetypeStore s = w.storeOf(id);
            int row = w.rowOf(id);
            s.intCol(Components.HEALTH)[row] = 10 + i;
            s.doubleCol(Components.POSITION)[row * 2 + Components.LANE_X] = i;
            s.doubleCol(Components.POSITION)[row * 2 + Components.LANE_Y] = -i;
        }
        return w;
    }

    @Test
    void createEntityMapsStableIdToRow() {
        ArchetypeWorld w = build();
        assertEquals(3, w.entityCount());
        for (int id = 0; id < 3; id++) {
            assertEquals(id, w.storeOf(id).entityAt(w.rowOf(id)), "карта и обратная карта согласованы");
            assertEquals(10 + id, w.storeOf(id).intCol(Components.HEALTH)[w.rowOf(id)]);
        }
    }

    /**
     * AC #3, ГЛАВНЫЙ: физический порядок строк различается, логическое состояние идентично →
     * checksum обязан совпасть. Иначе при подвижных строках DeterminismTest дал бы ЛОЖНОЕ
     * расхождение ref vs parallel.
     */
    @Test
    void checksumIsLayoutIndependent() {
        ArchetypeWorld a = build();
        ArchetypeWorld b = build();

        // Round-trip через супермножество: компоненты MOBISH не теряются, но строки переупорядочены.
        b.migrate(1, MOBISH_PLUS);
        b.migrate(1, MOBISH);

        ArchetypeStore sa = a.store(MOBISH), sb = b.store(MOBISH);
        assertEquals(3, sa.size());
        assertEquals(3, sb.size());
        assertNotEquals(sa.entityAt(1), sb.entityAt(1),
                "предусловие теста: физическая раскладка ДОЛЖНА различаться, иначе тест ничего не проверяет");
        assertEquals(2, sb.entityAt(1), "swap-remove затолкал e2 в освободившуюся строку 1");
        assertEquals(1, sb.entityAt(2), "мигрировавший e1 вернулся в хвост");

        assertEquals(a.checksum(), b.checksum(),
                "checksum обязан быть layout-independent: состояние то же, раскладка другая");
    }

    /** Обратная сторона: если чек-сумма нечувствительна ко ВСЕМУ — она бесполезна. */
    @Test
    void checksumReactsToStateChange() {
        ArchetypeWorld a = build();
        ArchetypeWorld b = build();
        assertEquals(a.checksum(), b.checksum(), "одинаковые миры — одинаковый хеш");

        b.storeOf(2).intCol(Components.HEALTH)[b.rowOf(2)] = 999;
        assertNotEquals(a.checksum(), b.checksum(), "изменение состояния обязано менять хеш");
    }

    /** Scratch-компоненты объявлены реестром и не должны попадать в чек-сумму. */
    @Test
    void checksumIgnoresScratchComponents() {
        long m = Components.mask(Components.HEALTH, Components.POWER_NEXT, Components.BUSY);
        ArchetypeWorld a = new ArchetypeWorld(Components.standard(), 2);
        int id = a.createEntity(m);
        long before = a.checksum();

        a.storeOf(id).intCol(Components.POWER_NEXT)[a.rowOf(id)] = 15;
        a.storeOf(id).longCol(Components.BUSY)[a.rowOf(id)] = 12345L;

        assertEquals(before, a.checksum(), "POWER_NEXT и BUSY — scratch, вне состояния мира");

        a.storeOf(id).intCol(Components.HEALTH)[a.rowOf(id)] = 1;
        assertNotEquals(before, a.checksum(), "а HEALTH — состояние");
    }

    /** AC #4: смена архетипа сохраняет общие компоненты и чинит карту переехавшего соседа. */
    @Test
    void migratePreservesStateAndFixesMapping() {
        ArchetypeWorld w = build();
        double x2 = w.storeOf(2).doubleCol(Components.POSITION)[w.rowOf(2) * 2 + Components.LANE_X];

        w.migrate(0, MOBISH_PLUS);

        assertEquals(MOBISH_PLUS, w.storeOf(0).mask, "энтити сменил архетип");
        assertEquals(10, w.storeOf(0).intCol(Components.HEALTH)[w.rowOf(0)], "общий компонент перенесён");
        assertEquals(0L, w.storeOf(0).longCol(Components.ENERGY)[w.rowOf(0)], "новый компонент — нулевой");

        // e2 переехал в дыру от e0 — карта обязана это отражать, иначе она разъедется молча.
        assertEquals(2, w.storeOf(2).entityAt(w.rowOf(2)));
        assertEquals(x2, w.storeOf(2).doubleCol(Components.POSITION)[w.rowOf(2) * 2 + Components.LANE_X]);
    }

    /** Смена архетипа видна в чек-сумме: набор компонентов — часть состояния. */
    @Test
    void checksumReactsToArchetypeChange() {
        ArchetypeWorld a = build();
        ArchetypeWorld b = build();
        b.migrate(0, MOBISH_PLUS);
        assertNotEquals(a.checksum(), b.checksum(), "смена набора компонентов обязана менять хеш");
    }

    @Test
    void copyIsDeepAndIdentical() {
        ArchetypeWorld a = build();
        ArchetypeWorld c = a.copy();
        assertEquals(a.checksum(), c.checksum());

        c.storeOf(0).intCol(Components.HEALTH)[c.rowOf(0)] = 777;
        assertNotEquals(a.checksum(), c.checksum(), "копия независима — правка не течёт в оригинал");
    }

    @Test
    void migrateToSameArchetypeIsNoop() {
        ArchetypeWorld w = build();
        long before = w.checksum();
        w.migrate(1, MOBISH);
        assertEquals(before, w.checksum());
        assertEquals(3, w.store(MOBISH).size());
    }

    @Test
    void deadOrUnknownEntityFailsLoudly() {
        ArchetypeWorld w = build();
        assertThrows(IndexOutOfBoundsException.class, () -> w.rowOf(99));
        assertThrows(IndexOutOfBoundsException.class, () -> w.rowOf(-1));
    }
}
