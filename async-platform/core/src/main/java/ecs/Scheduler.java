package ecs;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;

/**
 * Из объявленных reads/writes И матча архетипов строит стадии и исполняет их.
 *
 * Правило конфликта:
 *     conflict(A,B) ⇔ (пересечение компонентов) И (пересечение матчнутых архетипов)
 * Было — пересечение компонентов И пересечение диапазонов строк (Archetype.rangeOverlap).
 * Диапазоны оказались ПРОКСИ для «тот же архетип»: они disjoint по построению сцены, поэтому
 * пересечение строк было истинно ровно тогда, когда архетип общий. Матч по архетипам даёт ту же
 * раскладку, но не требует непрерывности строк — строки свободно двигаются (exploration #1).
 *
 * Планирование одноразовое (в конструкторе), поэтому матч считается простыми boolean[] —
 * никаких битсетов с искусственным потолком в 64 архетипа.
 * Детерминизм — фикс. порядок систем + упорядоченный apply отсортированных буферов.
 */
public final class Scheduler {
    public final List<GameSystem> systems;
    public List<int[]> stages;

    private boolean[][] matched; // [система][индекс архетипа]

    /**
     * Упорядоченный список масок, по которому построен текущий план. Ключ мемоизации.
     * УПОРЯДОЧЕННЫЙ, а не множество, и не storeCount: matched индексируется ИНДЕКСОМ store,
     * поэтому порядок — часть ключа, а одного лишь количества архетипов недостаточно.
     */
    private long[] planKey;

    public Scheduler(List<GameSystem> systems, ArchetypeWorld w) {
        this.systems = systems;
        ensurePlan(w);
    }

    /**
     * Пересчитывает раскладку, если множество архетипов мира изменилось. Зовётся в НАЧАЛЕ тика.
     *
     * ЧИСТАЯ ФУНКЦИЯ ОТ ПЕРЕДАННОГО МИРА — и это не стиль, а условие корректности: один Scheduler
     * гоняет ОБА мира (Main.java: сначала все тики ref, потом все тики par). Оставь раскладку в поле
     * «на потом» — и par стартует с раскладкой ФИНАЛЬНОГО тика ref. Тихо. Здесь же план всегда
     * выводится из масок ТОГО мира, который передали, поэтому порядок вызовов не важен.
     *
     * Детерминизм: план — чистая функция от (systems, упорядоченный список масок); список масок
     * после тика детерминирован, т.к. структурные операции идут только на барьере и в одном и том же
     * тотальном порядке в ref и par@N. Значит ref и par строят ОДИН план. См. решение #6.
     */
    private void ensurePlan(ArchetypeWorld w) {
        long[] key = new long[w.storeCount()];
        for (int a = 0; a < key.length; a++) key[a] = w.storeAt(a).mask;
        if (planKey != null && Arrays.equals(planKey, key)) return;
        this.matched = matchArchetypes(systems, w);
        this.stages = plan(systems, matched);
        this.planKey = key;
    }

    /** Архетип матчится системой, если он SUPERSET её component-query. */
    static boolean[][] matchArchetypes(List<GameSystem> systems, ArchetypeWorld w) {
        boolean[][] m = new boolean[systems.size()][w.storeCount()];
        for (int i = 0; i < systems.size(); i++) {
            long q = systems.get(i).query();
            for (int a = 0; a < w.storeCount(); a++) {
                m[i][a] = (w.storeAt(a).mask & q) == q;
            }
        }
        return m;
    }

    private static boolean componentConflict(GameSystem a, GameSystem b) {
        return (a.writes() & b.writes()) != 0 || (a.writes() & b.reads()) != 0 || (a.reads() & b.writes()) != 0;
    }

    /** Матчат ли системы хотя бы один общий архетип. */
    private static boolean archetypeOverlap(boolean[] a, boolean[] b) {
        for (int k = 0; k < a.length; k++) if (a[k] && b[k]) return true;
        return false;
    }

    /** conflict(A,B) ⇔ пересечение компонентов И пересечение матчнутых архетипов. */
    public static List<int[]> plan(List<GameSystem> systems, boolean[][] matched) {
        List<List<Integer>> stageSys = new ArrayList<>();
        for (int i = 0; i < systems.size(); i++) {
            int placed = -1;
            for (int st = 0; st < stageSys.size() && placed < 0; st++) {
                boolean conflict = false;
                for (int j : stageSys.get(st)) {
                    if (componentConflict(systems.get(i), systems.get(j))
                            && archetypeOverlap(matched[i], matched[j])) {
                        conflict = true;
                        break;
                    }
                }
                if (!conflict) placed = st;
            }
            if (placed < 0) { stageSys.add(new ArrayList<>()); placed = stageSys.size() - 1; }
            stageSys.get(placed).add(i);
        }
        List<int[]> out = new ArrayList<>();
        for (List<Integer> l : stageSys) {
            int[] a = new int[l.size()];
            for (int k = 0; k < a.length; k++) a[k] = l.get(k);
            out.add(a);
        }
        return out;
    }

    /** Однопоточный эталон: системы в порядке стадий, каждая — по своим архетипам. */
    public void runReference(ArchetypeWorld world) {
        ensurePlan(world);
        for (int[] stage : stages) {
            List<CommandBuffer> buffers = new ArrayList<>();
            world.freezeRows(); // инвариант C — заморозка и в эталоне тоже, иначе контракт зависел бы от режима
            try {
                for (int si : stage) {
                    GameSystem s = systems.get(si);
                    // По matched[si].length, а НЕ по живому world.storeCount(): архетип, рождённый
                    // структурной операцией в этом тике, ещё не имеет колонки в matched. Ходить по
                    // живому — это AIOOBE; ходить по matched — это «новый архетип виден с тика T+1»
                    // (решение #6), то есть семантика, а не заплатка.
                    for (int a = 0; a < matched[si].length; a++) {
                        if (!matched[si][a]) continue;
                        ArchetypeStore st = world.storeAt(a);
                        View v = new View(world.reg).bind(st, s.reads(), s.writes());
                        CommandBuffer cb = new CommandBuffer(si, st.mask, 0);
                        for (int row = 0; row < st.size(); row++) s.run(v, row, cb);
                        buffers.add(cb);
                    }
                }
            } finally {
                world.thawRows();
            }
            applyOrdered(world, buffers);
        }
    }

    /** Параллельно: внутри стадии — независимые задачи (система × архетип × чанк строк). */
    public void runParallel(ArchetypeWorld world, ExecutorService pool, int threads) throws Exception {
        ensurePlan(world);
        int chunkCount = Math.max(1, threads);
        for (int[] stage : stages) {
            List<Callable<CommandBuffer>> tasks = new ArrayList<>();
            for (int si : stage) {
                final GameSystem s = systems.get(si);
                final int sysIdx = si;
                for (int a = 0; a < matched[si].length; a++) { // см. комментарий в runReference
                    if (!matched[si][a]) continue;
                    final ArchetypeStore st = world.storeAt(a);
                    int[] bounds = chunkBounds(0, st.size(), chunkCount);
                    for (int c = 0; c < chunkCount; c++) {
                        final int lo = bounds[c], hi = bounds[c + 1], chunk = c;
                        if (lo == hi) continue;
                        tasks.add(() -> {
                            View v = new View(world.reg).bind(st, s.reads(), s.writes());
                            CommandBuffer cb = new CommandBuffer(sysIdx, st.mask, chunk);
                            for (int row = lo; row < hi; row++) s.run(v, row, cb);
                            return cb;
                        });
                    }
                }
            }
            List<CommandBuffer> buffers = new ArrayList<>(tasks.size());
            world.freezeRows(); // инвариант C: пока задачи бегут, строки под ними двигать нельзя
            try {
                List<Future<CommandBuffer>> fs = pool.invokeAll(tasks);
                for (Future<CommandBuffer> f : fs) buffers.add(f.get());
            } finally {
                world.thawRows();
            }
            applyOrdered(world, buffers);
        }
    }

    static int[] chunkBounds(int lo, int hi, int chunks) {
        int n = hi - lo;
        int[] b = new int[chunks + 1];
        int base = n / chunks, rem = n % chunks, idx = lo;
        for (int i = 0; i < chunks; i++) { b[i] = idx; idx += base + (i < rem ? 1 : 0); }
        b[chunks] = hi;
        return b;
    }

    /**
     * Упорядоченный apply: буферы сортируются по (systemOrder, archMask, chunkIndex), команды
     * применяются в порядке эмиссии. OP_SET на один таргет → last-writer по этому тотальному
     * порядку. Порядок не зависит ни от того, какой поток когда закончил, ни от порядка создания
     * архетипов (сортируем по МАСКЕ, а не по индексу store) → детерминизм.
     *
     * Единственное место cross-archetype доступа: цель — стабильный entityId, резолвится здесь,
     * на барьере. Карту и колонки берём РАЗ НА БАРЬЕР, а не на каждую из ~50k команд — вот это
     * замер сессии #4 подтвердил как реальный выигрыш. А вот перенос самого резолва в эмиссию
     * выигрыша НЕ дал и откачен: итерации apply независимы, промахи перекрываются внеочередным
     * исполнением, латентность здесь не узкое место.
     */
    static void applyOrdered(ArchetypeWorld world, List<CommandBuffer> buffers) {
        buffers.sort((a, b) -> {
            if (a.systemOrder != b.systemOrder) return Integer.compare(a.systemOrder, b.systemOrder);
            if (a.archMask != b.archMask) return Long.compare(a.archMask, b.archMask);
            return Integer.compare(a.chunkIndex, b.chunkIndex);
        });
        applyEffects(world, buffers);
        applyStructural(world, buffers);
    }

    /**
     * ФАЗА E — эффекты. Идёт ПЕРВОЙ, и это несёт всю нагрузку решения #8:
     * структурного ещё НЕ БЫЛО, значит карта и колонки не переаллоцированы и не сдвинуты —
     * кэши ниже валидны на весь проход. Именно поэтому горячий цикл не изменился по структуре:
     * замер сессии #4 (карта и колонки РАЗ НА БАРЬЕР, а не на каждую из ~50k команд) остаётся в силе.
     *
     * Побочно это и даёт «удаление побеждает внутри тика»: e, удаляемая в фазе S этого же барьера,
     * здесь ещё жива — эффект на неё применяется безвредно и уходит вместе со строкой. А e, удалённая
     * в ПРЕДЫДУЩЕМ барьере, имеет UNPLACED → бросок ниже. Громко, без единой новой проверки.
     */
    /**
     * Страйд таблицы колонок — КОНСТАНТА-степень двойки, а не живой reg.count(), и это ЗАМЕРЕНО.
     * При переменном страйде индекс si*comps+comp требует НАСТОЯЩЕГО IMUL, и его задержка ложится
     * на путь вычисления адреса загрузки colTab[...]; при константном сдвиг сворачивается в
     * адресацию. Разложение абляциями на сцене блок-энтити (par work=0, ~100k команд/тик):
     *   индекс si*comps+comp .... 0.040 мс  (89% цены дженерик-адресации)
     *   загрузка arityOf[comp] .. 0.0025 мс (ноль: ниже разрешения стенда)
     *   извлечение compOf(pk) ... 0.0025 мс (ноль)
     * Показательно, что compOf — тоже две инструкции, но стоит в 16 раз меньше: платит не их
     * количество, а зависимость АДРЕСА загрузки от результата IMUL.
     *
     * Цена решения: таблица storeCount*64 ссылок вместо storeCount*comps (~768 Б против ~120 Б на
     * трёх архетипах). Всё равно L1, строится раз на барьер, трогаются единицы строк.
     */
    private static final int COL_SHIFT = 6;
    private static final int COL_STRIDE = 1 << COL_SHIFT;

    static {
        // Страйд и потолок компонентов связаны ЖЁСТКО: подними MAX_COMPONENTS выше страйда — и comp
        // переполнится в биты si, а эффект МОЛЧА уедет в колонку чужого архетипа. Падаем здесь, при
        // загрузке класса, а не через сутки в проде на испорченном инвентаре.
        if (ComponentRegistry.MAX_COMPONENTS > COL_STRIDE)
            throw new IllegalStateException("COL_STRIDE=" + COL_STRIDE + " меньше потолка компонентов "
                    + ComponentRegistry.MAX_COMPONENTS + ": индекс (si << " + COL_SHIFT + ") | comp"
                    + " переполнит comp в биты архетипа. Поднял потолок — подними и COL_SHIFT.");
    }

    private static void applyEffects(ArchetypeWorld world, List<CommandBuffer> buffers) {
        long[] eLoc = world.entityLocMap();
        int comps = world.reg.count();
        // Плоская таблица [архетип][компонент] → INT-колонка. Плоская, а не вложенная: в серийном
        // цикле это ОДНА индирекция на команду, как и прежний invByStore. Архетипов и компонентов
        // единицы-десятки, таблица строится раз на барьер.
        int[][] colTab = new int[world.storeCount() << COL_SHIFT][];
        for (int a = 0; a < world.storeCount(); a++) {
            ArchetypeStore st = world.storeAt(a);
            for (int c = 0; c < comps; c++) colTab[(a << COL_SHIFT) | c] = st.intCol(c);
        }
        // Арность — тоже РАЗ НА БАРЬЕР. Прежний код держал её в локальной переменной, потому что
        // компонент был один; при дженерик-адресации reg.arity(comp) в цикле стал бы вызовом с
        // проверкой границ на КАЖДУЮ из ~50k команд — ровно та «работа в цикле», которую убрал замер
        // сессии #4 (memory #17).
        int[] arityOf = new int[comps];
        for (int c = 0; c < comps; c++) arityOf[c] = world.reg.arity(c);

        for (CommandBuffer cb : buffers) {
            for (int i = 0; i < cb.n; i++) {
                long loc = eLoc[cb.entity[i]]; // одна карта → один доступ к ней на команду, не два
                int si = ArchetypeWorld.storeIdxOf(loc);
                if (si < 0)
                    throw new IllegalStateException("команда на неразмещённую энтити e=" + cb.entity[i]);
                // Одна загрузка на op+comp+slot, дальше сдвиги в регистре: три отдельных массива
                // стоили 0.042 мс на тик (замер среза 7). См. раскладку в CommandBuffer.
                int pk = cb.packed[i];
                int comp = CommandBuffer.compOf(pk);
                int[] col = colTab[(si << COL_SHIFT) | comp];
                if (col == null)
                    throw new IllegalStateException("эффект на компонент без INT-колонки в архетипе: e="
                            + cb.entity[i] + " комп=" + world.reg.name(comp)
                            + " маска=" + Long.toBinaryString(world.storeAt(si).mask));
                int idx = ArchetypeWorld.rowOfLoc(loc) * arityOf[comp] + CommandBuffer.slotOf(pk);
                switch (CommandBuffer.opOf(pk)) {
                    case CommandBuffer.OP_ADD -> col[idx] += (int) cb.value[i];
                    case CommandBuffer.OP_SET -> col[idx]  = (int) cb.value[i];
                }
            }
        }
    }

    /**
     * ФАЗА S — структурные. Холодная: идёт по ОТДЕЛЬНОМУ потоку, поэтому стоит O(структурных),
     * а не O(всех команд) — сцены без структурной текучки не платят здесь НИЧЕГО (пустой sn).
     * Кэши фазы E сюда НЕ переносятся сознательно: createEntity реаллоцирует entityLoc, storeIndex
     * добавляет архетип, addRow/swapRemove двигают колонки — любой кэш здесь протух бы МОЛЧА.
     *
     * Политика решения #8: цель мертва И удалена В ЭТОМ барьере → идемпотентный no-op (удаление
     * победило: две системы, решившие убить одного моба, — законный геймплей, а не крах).
     * Цель мертва И удалена РАНЬШЕ → громкий бросок (это stale-ссылка, то есть ошибка).
     * Различить по entityLoc нельзя — UNPLACED в обоих случаях, поэтому множество ведётся явно.
     */
    private static void applyStructural(ArchetypeWorld world, List<CommandBuffer> buffers) {
        Set<Integer> destroyedHere = null;
        for (CommandBuffer cb : buffers) {
            if (cb.sn == 0) continue; // быстрый выход: сцена без текучки
            int[] h2id = cb.handleCount() > 0 ? new int[cb.handleCount()] : null;
            for (int i = 0; i < cb.sn; i++) {
                int op = cb.sop[i];
                if (op == CommandBuffer.OP_CREATE) {
                    // id выдаётся ЗДЕСЬ, по позиции в тотальном порядке (решение #7) — общий счётчик
                    // при эмиссии дал бы недетерминизм: порядок эмиссии зависит от потоков.
                    h2id[CommandBuffer.handleIndex(cb.starget[i])] = world.createEntity(cb.svalue[i]);
                    continue;
                }
                int t = resolveTarget(cb.starget[i], h2id);
                if (!world.isAlive(t)) {
                    if (destroyedHere != null && destroyedHere.contains(t)) continue; // удаление победило
                    throw new IllegalStateException("структурная команда на неразмещённую энтити e=" + t
                            + " (op=" + op + "): ссылка на удалённую РАНЬШЕ этого барьера");
                }
                switch (op) {
                    case CommandBuffer.OP_DESTROY -> {
                        world.destroyEntity(t);
                        if (destroyedHere == null) destroyedHere = new HashSet<>();
                        destroyedHere.add(t);
                    }
                    case CommandBuffer.OP_ADD_COMPONENT    -> world.addComponent(t, cb.scomp[i]);
                    case CommandBuffer.OP_REMOVE_COMPONENT -> world.removeComponent(t, cb.scomp[i]);
                    case CommandBuffer.OP_INIT -> initValue(world, t, cb.scomp[i], cb.sslot[i], cb.svalue[i]);
                }
            }
        }
    }

    private static int resolveTarget(int target, int[] h2id) {
        if (!CommandBuffer.isHandle(target)) return target;
        if (h2id == null) throw new IllegalStateException("дескриптор " + target + " в буфере без create");
        return h2id[CommandBuffer.handleIndex(target)];
    }

    /** Запись начального значения. Холодная фаза — switch по kind здесь ничего не стоит. */
    private static void initValue(ArchetypeWorld world, int id, int comp, int lane, long v) {
        ArchetypeStore st = world.storeOf(id);
        if (!st.has(comp))
            throw new IllegalStateException("init компонента, которого нет в архетипе: e=" + id
                    + " комп=" + world.reg.name(comp) + " маска=" + Long.toBinaryString(st.mask));
        int idx = world.rowOf(id) * world.reg.arity(comp) + lane;
        switch (world.reg.kind(comp)) {
            case INT    -> st.intCol(comp)[idx]    = (int) v;
            case LONG   -> st.longCol(comp)[idx]   = v;
            case DOUBLE -> st.doubleCol(comp)[idx] = Double.longBitsToDouble(v);
        }
    }
}
