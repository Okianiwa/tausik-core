package ecs;

import java.util.Arrays;

/**
 * Thread-local буфер отложенных эффектов одной задачи (система × архетип × чанк строк).
 * Эффекты на ЧУЖОЙ инвентарь (хоппер толкает предмет в соседа) нельзя писать на месте
 * в параллельной фазе — они уходят сюда и применяются в упорядоченной apply-фазе.
 *
 * Тег (systemOrder, archMask, chunkIndex). Был (systemOrder, chunkStart), где chunkStart —
 * индекс строки: при подвижных строках он теряет смысл, а чанки теперь нумеруются внутри
 * архетипа, поэтому chunkStart перестал быть уникальным между архетипами.
 * Сортировка по МАСКЕ, а не по id создания архетипа: id зависит от истории создания store'ов,
 * маска — нет, поэтому тотальный порядок apply воспроизводим независимо от порядка сборки сцены.
 *
 * Буфер уже отсортирован внутри (строки по возрастанию), глобальный детерминизм — сортировкой
 * БУФЕРОВ (их немного), без per-command сортировки.
 *
 * Цель — СТАБИЛЬНЫЙ entityId, резолв происходит в applyOrdered. Резолвить при эмиссии (в
 * параллельной фазе, где строки заморожены) ПРОБОВАЛИ и ОТКАТИЛИ: замер сессии #4 показал ноль
 * выигрыша — цикл apply не имеет зависимостей между итерациями, поэтому внеочередное исполнение и
 * так перекрывает промахи кэша, они не латентность-bound. См. dead end.
 */
public final class CommandBuffer {
    public static final int OP_ADD = 0;  // коммутативная (перекладка предметов)
    public static final int OP_SET = 1;  // порядко-зависимая (last-writer по расписанию)

    // Структурные операции. Отдельный поток, а не новые значения op выше — см. решение #8:
    // фаза S обязана стоить O(структурных), а не O(всех ~50k команд), иначе сцены без структурной
    // текучки платят за неё на каждом барьере. Раздельные потоки — это требование «регресса нет»,
    // а не вкусовщина.
    public static final int OP_CREATE           = 0;  // svalue = маска архетипа, starget = дескриптор
    public static final int OP_DESTROY          = 1;
    public static final int OP_ADD_COMPONENT    = 2;
    public static final int OP_REMOVE_COMPONENT = 3;
    public static final int OP_INIT             = 4;  // начальное значение новорождённой энтити

    public final int systemOrder;
    public final long archMask;
    public final int chunkIndex;

    /*
     * --- поток ЭФФЕКТОВ (горячий: ~50k команд/тик у хоппера) ---
     *
     * op, comp и slot едут В ОДНОМ int, а не тремя массивами, и это ЗАМЕРЕНО, а не вкусовщина.
     * Дженериковость командного пути (буфер обязан нести id компонента — иначе не init'ить
     * произвольный компонент) стоила на срезе 7 +0.092 мс серийной apply на тик, из которых
     * 0.042 мс — ровно отдельный массив comp[]: лишняя запись на каждую из ~50k команд плюс
     * лишний массив в копировании при росте буфера (8→8k = 10 удвоений за тик). Упаковка
     * снимает это целиком: потоков 3 (packed, entity, value) вместо 5, и даже меньше, чем было
     * ДО дженериковости (4). В apply — одна загрузка и три сдвига вместо трёх загрузок.
     *
     * Раскладка: op в битах 24+, comp в 16..23, slot в 0..15. Ширины с запасом на порядок:
     * comp < 64 by construction (маски архетипа — биты long), slot — индекс ячейки инвентаря
     * либо lane компонента, единицы. Выход за диапазон ловится в push() ГРОМКО: молчаливое
     * переполнение здесь испортило бы чужую строку, а не упало.
     */
    int[] packed = new int[8];
    int[] entity = new int[8];  // СТАБИЛЬНЫЙ entityId: apply резолвит его в (архетип, строку)
    long[] value = new long[8];
    int n = 0;

    private static final int COMP_SHIFT = 16, OP_SHIFT = 24;
    private static final int SLOT_MASK = 0xFFFF, COMP_MASK = 0xFF;

    static int opOf(int packed)   { return packed >>> OP_SHIFT; }
    static int compOf(int packed) { return (packed >>> COMP_SHIFT) & COMP_MASK; }
    static int slotOf(int packed) { return packed & SLOT_MASK; }

    /*
     * --- поток СТРУКТУРНЫХ (холодный: единицы-десятки за барьер) ---
     *
     * ЛЕНИВЫЙ: массивы рождаются при первой структурной команде, а не в конструкторе. Это НЕ
     * оптимизация — ЗАМЕРЕНО, выигрыш НОЛЬ (par work=0: 0.553 → 0.548 при ±0.019, то есть ниже
     * разрешения стенда). Сделано ради ЧЕСТНОСТИ УТВЕРЖДЕНИЯ: комментарий фазы S обещает, что
     * «сцены без структурной текучки не платят здесь НИЧЕГО», а при жадной аллокации сцена без
     * единого create платила 5 массивов на КАЖДЫЙ буфер — обещание было ложью, пусть и дешёвой.
     * Не искать здесь ускорения: остаток регресса среза 7 (+0.033 мс par) сидит НЕ ТУТ.
     */
    int[] sop, starget, scomp, sslot;  // starget: entityId (>=0) либо дескриптор (<0), см. newHandle()
    long[] svalue;                     // OP_CREATE: маска архетипа; OP_INIT: значение
    int sn = 0;

    private int handles = 0;

    public CommandBuffer(int systemOrder, long archMask, int chunkIndex) {
        this.systemOrder = systemOrder;
        this.archMask = archMask;
        this.chunkIndex = chunkIndex;
    }

    public void addInv(int entityId, int slot, long amount) { push(OP_ADD, entityId, Components.INVENTORY, slot, amount); }
    public void setInv(int entityId, int slot, long v)      { push(OP_SET, entityId, Components.INVENTORY, slot, v); }

    /** Дженерик-эффекты: компонент задаётся id, а не зашит в apply (AC #1). */
    public void add(int entityId, int comp, int lane, long amount) { push(OP_ADD, entityId, comp, lane, amount); }
    public void set(int entityId, int comp, int lane, long v)      { push(OP_SET, entityId, comp, lane, v); }

    /**
     * Создать энтити архетипа mask. Возвращает ДЕСКРИПТОР (всегда < 0), а не entityId: реального id
     * ещё не существует — он выдаётся в фазе S по позиции этого OP_CREATE в тотальном порядке
     * (решение #7). Общий счётчик при эмиссии запрещён: порядок эмиссии зависит от потоков.
     * Дескриптор локален СВОЕМУ буферу — буфер пишет ровно один поток, поэтому счётчик без синхронизации.
     */
    public int create(long mask) {
        int h = newHandle();
        spush(OP_CREATE, h, 0, 0, mask);
        return h;
    }

    /**
     * Начальное значение новорождённой (target — дескриптор из create) либо уже живой энтити.
     * Едет в СТРУКТУРНОМ потоке, потому что в фазе эффектов новорождённой ещё не существует
     * (решение #8): инициализировать её обычным set() следом физически невозможно.
     */
    public void init(int target, int comp, int lane, long v) { spush(OP_INIT, target, comp, lane, v); }

    /** То же для DOUBLE-колонок: значение едет битами, чтобы поток остался одним long[]. */
    public void initDouble(int target, int comp, int lane, double v) {
        spush(OP_INIT, target, comp, lane, Double.doubleToLongBits(v));
    }

    public void destroy(int target)                  { spush(OP_DESTROY, target, 0, 0, 0); }
    public void addComponent(int target, int comp)   { spush(OP_ADD_COMPONENT, target, comp, 0, 0); }
    public void removeComponent(int target, int comp){ spush(OP_REMOVE_COMPONENT, target, comp, 0, 0); }

    /** Дескрипторы: -1, -2, ... Реальные entityId всегда >= 0, поэтому знак и различает их. */
    private int newHandle() { return -(++handles); }

    static boolean isHandle(int target) { return target < 0; }
    static int handleIndex(int target) { return -target - 1; }

    int handleCount() { return handles; }

    private void push(int o, int e, int c, int s, long v) {
        // Гард диапазона — на горячем пути, но это предсказуемая ветка против ТИХОЙ порчи чужой
        // строки: переполнись slot в биты comp, и эффект уедет в другой компонент без единого следа.
        if ((c & ~COMP_MASK) != 0 || (s & ~SLOT_MASK) != 0)
            throw new IllegalArgumentException("не влезает в упакованную команду: комп=" + c
                    + " (макс " + COMP_MASK + "), slot=" + s + " (макс " + SLOT_MASK + ")");
        if (n == packed.length) {
            int cap = n * 2;
            packed = Arrays.copyOf(packed, cap);
            entity = Arrays.copyOf(entity, cap);
            value = Arrays.copyOf(value, cap);
        }
        packed[n] = (o << OP_SHIFT) | (c << COMP_SHIFT) | s;
        entity[n] = e;
        value[n] = v;
        n++;
    }

    private void spush(int o, int t, int c, int s, long v) {
        if (sop == null) {
            sop = new int[4]; starget = new int[4]; scomp = new int[4]; sslot = new int[4];
            svalue = new long[4];
        } else if (sn == sop.length) {
            int cap = sn * 2;
            sop = Arrays.copyOf(sop, cap);
            starget = Arrays.copyOf(starget, cap);
            scomp = Arrays.copyOf(scomp, cap);
            sslot = Arrays.copyOf(sslot, cap);
            svalue = Arrays.copyOf(svalue, cap);
        }
        sop[sn] = o; starget[sn] = t; scomp[sn] = c; sslot[sn] = s; svalue[sn] = v; sn++;
    }
}
