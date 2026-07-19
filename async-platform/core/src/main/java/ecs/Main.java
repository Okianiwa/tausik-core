package ecs;

import ecs.scene.BlockEntityScene;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * CLI-раннер среза: печатает раскладку систем по стадиям (замер конфликтности) и проверяет
 * детерминизм checksum(reference)==checksum(parallel). Прод-замер speedup — в JMH (TickBenchmark).
 * Аргументы: [N] [ticks] [threads]. По умолчанию 150000 50 8 (пул по физ.ядрам).
 */
public final class Main {

    public static void main(String[] args) throws Exception {
        int n       = args.length > 0 ? Integer.parseInt(args[0]) : 150_000;
        int ticks   = args.length > 1 ? Integer.parseInt(args[1]) : 50;
        int threads = args.length > 2 ? Integer.parseInt(args[2]) : 8;

        List<GameSystem> systems = BlockEntityScene.systems();
        ArchetypeWorld ref = BlockEntityScene.build(n);
        ArchetypeWorld par = BlockEntityScene.build(n);
        Scheduler sched = new Scheduler(systems, ref);

        System.out.printf("Сцена: N=%d блок-энтити, ticks=%d, threads=%d%n", n, ticks, threads);
        printStages(sched, ref.reg);

        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            for (int t = 0; t < ticks; t++) sched.runReference(ref);
            for (int t = 0; t < ticks; t++) sched.runParallel(par, pool, threads);
        } finally {
            pool.shutdown();
        }

        long cRef = ref.checksum(), cPar = par.checksum();
        boolean det = cRef == cPar;
        System.out.printf("%nДетерминизм: checksum(ref)=%d checksum(par@%d)=%d → %s%n",
                cRef, threads, cPar, det ? "СОВПАЛО ✓" : "РАСХОЖДЕНИЕ ✗");
        if (!det) throw new IllegalStateException("Недетерминизм: ref != parallel");
    }

    /** Имена компонентов берутся из реестра — раньше здесь был захардкоженный массив. */
    static void printStages(Scheduler s, ComponentRegistry reg) {
        System.out.printf("Стадий: %d (систем всего: %d)%n", s.stages.size(), s.systems.size());
        for (int st = 0; st < s.stages.size(); st++) {
            int[] stage = s.stages.get(st);
            System.out.printf("  Стадия %d [%d систем]:%n", st, stage.length);
            for (int si : stage) {
                GameSystem g = s.systems.get(si);
                System.out.printf("    %-15s r{%s} w{%s}%n", g.name(), names(g.reads(), reg), names(g.writes(), reg));
            }
        }
    }

    static String names(long mask, ComponentRegistry reg) {
        StringBuilder sb = new StringBuilder();
        for (int c = 0; c < reg.count(); c++)
            if ((mask & Components.bit(c)) != 0) { if (sb.length() > 0) sb.append(','); sb.append(reg.name(c)); }
        return sb.toString();
    }

    private Main() {}
}
