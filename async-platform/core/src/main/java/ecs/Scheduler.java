package ecs;

import java.util.ArrayList;
import java.util.List;
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
    public final List<int[]> stages;

    private final boolean[][] matched; // [система][индекс архетипа]

    public Scheduler(List<GameSystem> systems, ArchetypeWorld w) {
        this.systems = systems;
        this.matched = matchArchetypes(systems, w);
        this.stages = plan(systems, matched);
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
        for (int[] stage : stages) {
            List<CommandBuffer> buffers = new ArrayList<>();
            for (int si : stage) {
                GameSystem s = systems.get(si);
                for (int a = 0; a < world.storeCount(); a++) {
                    if (!matched[si][a]) continue;
                    ArchetypeStore st = world.storeAt(a);
                    View v = new View(world.reg).bind(st, s.reads(), s.writes());
                    CommandBuffer cb = new CommandBuffer(si, st.mask, 0);
                    for (int row = 0; row < st.size(); row++) s.run(v, row, cb);
                    buffers.add(cb);
                }
            }
            applyOrdered(world, buffers);
        }
    }

    /** Параллельно: внутри стадии — независимые задачи (система × архетип × чанк строк). */
    public void runParallel(ArchetypeWorld world, ExecutorService pool, int threads) throws Exception {
        int chunkCount = Math.max(1, threads);
        for (int[] stage : stages) {
            List<Callable<CommandBuffer>> tasks = new ArrayList<>();
            for (int si : stage) {
                final GameSystem s = systems.get(si);
                final int sysIdx = si;
                for (int a = 0; a < world.storeCount(); a++) {
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
            List<Future<CommandBuffer>> fs = pool.invokeAll(tasks);
            List<CommandBuffer> buffers = new ArrayList<>(fs.size());
            for (Future<CommandBuffer> f : fs) buffers.add(f.get());
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
     * на барьере, где индирекция дёшева (команд мало относительно полезной работы).
     */
    static void applyOrdered(ArchetypeWorld world, List<CommandBuffer> buffers) {
        buffers.sort((a, b) -> {
            if (a.systemOrder != b.systemOrder) return Integer.compare(a.systemOrder, b.systemOrder);
            if (a.archMask != b.archMask) return Long.compare(a.archMask, b.archMask);
            return Integer.compare(a.chunkIndex, b.chunkIndex);
        });
        int arity = world.reg.arity(Components.INVENTORY);
        for (CommandBuffer cb : buffers) {
            for (int i = 0; i < cb.n; i++) {
                int e = cb.entity[i];
                int[] col = world.storeOf(e).intCol(Components.INVENTORY);
                int idx = world.rowOf(e) * arity + cb.slot[i];
                switch (cb.op[i]) {
                    case CommandBuffer.OP_ADD -> col[idx] += (int) cb.value[i];
                    case CommandBuffer.OP_SET -> col[idx]  = (int) cb.value[i];
                }
            }
        }
    }
}
