package ecs;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Контейнер архетипов + карта стабильный entityId ↔ (архетип, localRow).
 * Заменяет фикс-колонки старого World: знание о конкретных компонентах живёт в ComponentRegistry,
 * данные — в ArchetypeStore по маске. Новая подсистема больше не правит этот класс.
 *
 * ВРЕМЕННОЕ ИМЯ: станет World на шаге 9, когда фикс-колоночный World будет удалён. Пока оба живут
 * параллельно — иначе рефакторинг такого размера не имел бы ни одного зелёного промежуточного шага.
 */
public final class ArchetypeWorld {
    public final ComponentRegistry reg;
    private final List<ArchetypeStore> stores = new ArrayList<>();

    // entityId → индекс в stores (-1 = не создан) и localRow внутри него.
    private int[] entityStore;
    private int[] entityRow;
    private int entityCount;

    public ArchetypeWorld(ComponentRegistry reg, int expectedEntities) {
        this.reg = reg;
        int cap = Math.max(1, expectedEntities);
        entityStore = new int[cap];
        entityRow = new int[cap];
        Arrays.fill(entityStore, -1);
    }

    public int entityCount() { return entityCount; }
    public List<ArchetypeStore> stores() { return stores; }
    public int storeCount() { return stores.size(); }

    /** Индекс архетипа по маске; создаёт при первом обращении. Архетипов единицы — линейный поиск дешевле хеша. */
    public int storeIndex(long mask) {
        for (int i = 0; i < stores.size(); i++) if (stores.get(i).mask == mask) return i;
        stores.add(new ArchetypeStore(reg, mask, 16));
        return stores.size() - 1;
    }

    public ArchetypeStore store(long mask) { return stores.get(storeIndex(mask)); }
    public ArchetypeStore storeAt(int index) { return stores.get(index); }

    public ArchetypeStore storeOf(int entityId) { return stores.get(entityStore[checkAlive(entityId)]); }
    public int storeIndexOf(int entityId) { return entityStore[checkAlive(entityId)]; }
    public int rowOf(int entityId) { return entityRow[checkAlive(entityId)]; }

    /**
     * Создаёт энтити в архетипе mask, возвращает СТАБИЛЬНЫЙ entityId.
     * Это построение сцены, а не deferred-структурное изменение из параллельной фазы
     * (те — вне объёма задачи, см. scope_exclude).
     */
    public int createEntity(long mask) {
        int id = entityCount++;
        ensureCapacity(id + 1);
        int si = storeIndex(mask);
        entityStore[id] = si;
        entityRow[id] = stores.get(si).addRow(id);
        return id;
    }

    /**
     * Смена архетипа: swap-remove из старого + append в новый, общие компоненты переносятся.
     * ПЕРЕСТАВЛЯЕТ СТРОКИ — переехавшему соседу чинится карта. Именно поэтому checksum обязан
     * ключеваться на entityId, а не на индексе строки (иначе перестановка = ложное расхождение).
     */
    public void migrate(int entityId, long newMask) {
        int id = checkAlive(entityId);
        int oldIdx = entityStore[id];
        ArchetypeStore old = stores.get(oldIdx);
        if (old.mask == newMask) return;

        int newIdx = storeIndex(newMask);
        ArchetypeStore dst = stores.get(newIdx);
        int oldRow = entityRow[id];

        int dstRow = dst.addRow(id);
        old.copyRowTo(oldRow, dst, dstRow);

        int moved = old.swapRemove(oldRow);
        if (moved >= 0) entityRow[moved] = oldRow; // переехавший занял освободившуюся строку

        entityStore[id] = newIdx;
        entityRow[id] = dstRow;
    }

    /**
     * Детерминированный чек-сумм. Ключ — СТАБИЛЬНЫЙ entityId, не индекс строки: физическая
     * раскладка (swap-remove, порядок создания архетипов) на него не влияет. Старый World хешировал
     * в row-order — при подвижных строках это дало бы расхождение ref vs parallel при ИДЕНТИЧНОМ
     * состоянии, то есть детерминизм-тест врал бы. Scratch-компоненты исключает реестр (inChecksum).
     */
    public long checksum() {
        long h = 1125899906842597L;
        for (int id = 0; id < entityCount; id++) {
            int si = entityStore[id];
            if (si < 0) continue;
            ArchetypeStore s = stores.get(si);
            int row = entityRow[id];
            h = 31 * h + id;
            h = 31 * h + s.mask; // архетип — часть состояния: смена набора компонентов видна в хеше
            for (int c = 0; c < reg.count(); c++) {
                if (!s.has(c) || !reg.inChecksum(c)) continue;
                int a = reg.arity(c);
                for (int lane = 0; lane < a; lane++) {
                    int i = row * a + lane;
                    switch (reg.kind(c)) {
                        case INT    -> h = 31 * h + s.intCol(c)[i];
                        case LONG   -> h = 31 * h + s.longCol(c)[i];
                        case DOUBLE -> h = 31 * h + Double.doubleToLongBits(s.doubleCol(c)[i]);
                    }
                }
            }
        }
        return h;
    }

    /** Глубокая копия мира — reference-прогон против parallel идут по независимым данным. */
    public ArchetypeWorld copy() {
        ArchetypeWorld w = new ArchetypeWorld(reg, Math.max(1, entityCount));
        for (ArchetypeStore s : stores) w.stores.add(s.copy());
        w.ensureCapacity(Math.max(1, entityCount));
        System.arraycopy(entityStore, 0, w.entityStore, 0, entityCount);
        System.arraycopy(entityRow, 0, w.entityRow, 0, entityCount);
        w.entityCount = entityCount;
        return w;
    }

    private int checkAlive(int entityId) {
        if (entityId < 0 || entityId >= entityCount)
            throw new IndexOutOfBoundsException("entityId=" + entityId + " count=" + entityCount);
        if (entityStore[entityId] < 0)
            throw new IllegalStateException("entityId=" + entityId + " не размещён в архетипе");
        return entityId;
    }

    private void ensureCapacity(int need) {
        if (need <= entityStore.length) return;
        int cap = Math.max(need, entityStore.length * 2);
        int from = entityStore.length;
        entityStore = Arrays.copyOf(entityStore, cap);
        entityRow = Arrays.copyOf(entityRow, cap);
        Arrays.fill(entityStore, from, cap, -1);
    }
}
