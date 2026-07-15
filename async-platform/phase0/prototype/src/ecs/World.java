package ecs;

/**
 * Колоночное (SoA) хранилище компонентов для той-ECS. Фикс. набор компонентов (Phase 0).
 * Каждый компонент = отдельный непрерывный массив → cache-friendly, префетчится (важно для X3D L3).
 */
public final class World {
    // id компонента = индекс бита в маске reads/writes
    public static final int POS = 0, ENERGY = 1, HEAT = 2, PROGRESS = 3, INV = 4;
    public static final int COMPONENT_COUNT = 5;
    public static long bit(int comp) { return 1L << comp; }

    public final int size;

    // Прямо-писуемые колонки (пишутся системами в параллельной фазе, disjoint by construction)
    public final int[] posX, posY, posZ;   // POS (read-mostly)
    public final long[] energy;            // ENERGY
    public final double[] heat;            // HEAT
    public final int[] progress;           // PROGRESS
    public final long[] inv;               // INV

    // Колонка, пишущаяся ТОЛЬКО в apply-фазе через команды (deferred effect) — чистое разделение
    public final long[] received;

    public World(int size) {
        this.size = size;
        posX = new int[size]; posY = new int[size]; posZ = new int[size];
        energy = new long[size];
        heat = new double[size];
        progress = new int[size];
        inv = new long[size];
        received = new long[size];
    }

    /** Детерминированный чек-сумм всего состояния мира — для диффа детерминизма ref vs parallel. */
    public long checksum() {
        long h = 1125899906842597L;
        for (int i = 0; i < size; i++) {
            h = 31 * h + posX[i]; h = 31 * h + posY[i]; h = 31 * h + posZ[i];
            h = 31 * h + energy[i];
            h = 31 * h + Double.doubleToLongBits(heat[i]);
            h = 31 * h + progress[i];
            h = 31 * h + inv[i];
            h = 31 * h + received[i];
        }
        return h;
    }
}
