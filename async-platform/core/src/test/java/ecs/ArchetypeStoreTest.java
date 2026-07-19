package ecs;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** Реестр компонентов + хранилище архетипа: разреженность, swap-remove, границы. */
class ArchetypeStoreTest {

    @Test
    void standardRegistryMatchesConstants() {
        ComponentRegistry r = Components.standard();
        assertEquals(Components.COUNT, r.count());
        assertEquals(Components.SLOTS, r.arity(Components.INVENTORY), "INVENTORY = int x SLOTS");
        assertEquals(2, r.arity(Components.POSITION), "POSITION хранит x,y в одной колонке");
        assertEquals(2, r.arity(Components.VELOCITY));
        assertEquals(ComponentRegistry.Kind.LONG, r.kind(Components.ENERGY));
        assertEquals(ComponentRegistry.Kind.DOUBLE, r.kind(Components.HEAT));
        assertEquals(ComponentRegistry.Kind.INT, r.kind(Components.PROGRESS));
    }

    /** AC #9: маска reads/writes — long, 65-й компонент обязан падать громко, не терять бит. */
    @Test
    void registryRefusesMoreThan64Components() {
        ComponentRegistry r = new ComponentRegistry();
        for (int i = 0; i < ComponentRegistry.MAX_COMPONENTS; i++) {
            r.register("c" + i, ComponentRegistry.Kind.INT, 1);
        }
        assertEquals(64, r.count());
        IllegalStateException ex = assertThrows(IllegalStateException.class,
                () -> r.register("overflow", ComponentRegistry.Kind.INT, 1));
        assertTrue(ex.getMessage().contains("64"), "сообщение должно называть потолок: " + ex.getMessage());
        assertTrue(ex.getMessage().contains("overflow"), "и имя виновника: " + ex.getMessage());
    }

    @Test
    void registryRejectsBadArity() {
        ComponentRegistry r = new ComponentRegistry();
        assertThrows(IllegalArgumentException.class, () -> r.register("bad", ComponentRegistry.Kind.INT, 0));
    }

    /** AC #2: добавление компонента, которого нет в Components — без правки World/View. */
    @Test
    void newComponentNeedsNoWorldEdit() {
        ComponentRegistry r = Components.standard();
        int mana = r.register("MANA", ComponentRegistry.Kind.DOUBLE, 1);
        assertEquals(Components.COUNT, mana, "новый id идёт следом за стандартными");

        ArchetypeStore s = new ArchetypeStore(r, Components.bit(mana), 4);
        int row = s.addRow(7);
        s.doubleCol(mana)[row] = 42.5;
        assertEquals(42.5, s.doubleCol(mana)[row]);
    }

    /** Разреженность: архетип аллоцирует ТОЛЬКО свои компоненты. */
    @Test
    void storeAllocatesOnlyItsOwnComponents() {
        ComponentRegistry r = Components.standard();
        long mask = Components.mask(Components.INVENTORY, Components.LINK);
        ArchetypeStore s = new ArchetypeStore(r, mask, 8);

        assertTrue(s.has(Components.INVENTORY));
        assertTrue(s.has(Components.LINK));
        assertFalse(s.has(Components.ENERGY));
        assertNotNull(s.intCol(Components.INVENTORY));
        assertNotNull(s.intCol(Components.LINK));
        assertNull(s.longCol(Components.ENERGY), "чужой компонент не аллоцируется — это и есть sparse");
        assertNull(s.doubleCol(Components.HEAT));
    }

    @Test
    void addRowTracksEntityIdAndGrows() {
        ComponentRegistry r = Components.standard();
        ArchetypeStore s = new ArchetypeStore(r, Components.mask(Components.HEALTH), 1);
        for (int i = 0; i < 10; i++) {
            int row = s.addRow(100 + i);
            s.intCol(Components.HEALTH)[row] = i;
        }
        assertEquals(10, s.size());
        assertTrue(s.capacity() >= 10, "ёмкость растёт удвоением");
        for (int i = 0; i < 10; i++) {
            assertEquals(100 + i, s.entityAt(i));
            assertEquals(i, s.intCol(Components.HEALTH)[i]);
        }
    }

    /** swap-remove: дыра затыкается последней строкой; данные переехавшего сохранены. */
    @Test
    void swapRemoveMovesLastRowIntoHole() {
        ComponentRegistry r = Components.standard();
        ArchetypeStore s = new ArchetypeStore(r, Components.mask(Components.HEALTH, Components.POSITION), 4);
        for (int i = 0; i < 4; i++) {
            int row = s.addRow(200 + i);
            s.intCol(Components.HEALTH)[row] = i * 10;
            s.doubleCol(Components.POSITION)[row * 2 + Components.LANE_X] = i;
            s.doubleCol(Components.POSITION)[row * 2 + Components.LANE_Y] = -i;
        }
        int moved = s.swapRemove(1);

        assertEquals(203, moved, "в дыру переехала последняя строка");
        assertEquals(3, s.size());
        assertEquals(203, s.entityAt(1));
        assertEquals(30, s.intCol(Components.HEALTH)[1], "данные переехавшего целы");
        assertEquals(3.0, s.doubleCol(Components.POSITION)[1 * 2 + Components.LANE_X]);
        assertEquals(-3.0, s.doubleCol(Components.POSITION)[1 * 2 + Components.LANE_Y]);
        assertEquals(200, s.entityAt(0), "соседи не тронуты");
        assertEquals(202, s.entityAt(2));
    }

    @Test
    void swapRemoveOfLastRowMovesNobody() {
        ComponentRegistry r = Components.standard();
        ArchetypeStore s = new ArchetypeStore(r, Components.mask(Components.HEALTH), 4);
        s.addRow(1);
        s.addRow(2);
        assertEquals(-1, s.swapRemove(1), "удаление последней строки никого не двигает");
        assertEquals(1, s.size());
        assertEquals(1, s.entityAt(0));
    }

    /** Смена архетипа на уровне данных: общие компоненты переносятся, чужие отбрасываются. */
    @Test
    void copyRowToTransfersCommonComponentsOnly() {
        ComponentRegistry r = Components.standard();
        ArchetypeStore src = new ArchetypeStore(r, Components.mask(Components.HEALTH, Components.LINK), 2);
        ArchetypeStore dst = new ArchetypeStore(r, Components.mask(Components.HEALTH, Components.ENERGY), 2);

        int sRow = src.addRow(5);
        src.intCol(Components.HEALTH)[sRow] = 77;
        src.intCol(Components.LINK)[sRow] = 9;

        int dRow = dst.addRow(5);
        src.copyRowTo(sRow, dst, dRow);

        assertEquals(77, dst.intCol(Components.HEALTH)[dRow], "общий компонент перенесён");
        assertEquals(0L, dst.longCol(Components.ENERGY)[dRow], "новый компонент — нулевой");
        assertFalse(dst.has(Components.LINK), "LINK отброшен: его нет в целевой маске");
    }
}
