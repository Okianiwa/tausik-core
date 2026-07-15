package ecs;

/**
 * Единица логики одного тика над множеством энтити.
 * Объявляет reads/writes (маски компонентов) — контракт, на который опирается планировщик.
 */
public interface GameSystem {
    String name();
    long reads();
    long writes();
    /** Выполнить логику для одной энтити. Структурные эффекты — только через CommandBuffer. */
    void run(View v, int entity, CommandBuffer cb);
}
