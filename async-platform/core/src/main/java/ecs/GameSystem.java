package ecs;

/**
 * Единица логики одного тика над множеством энтити. Объявляет reads/writes —
 * контракт, на который опирается планировщик при раскладке по стадиям.
 */
public interface GameSystem {
    String name();
    long reads();
    long writes();
    /** Архетип, над которым работает система. UNIVERSAL — весь мир (по умолчанию). */
    default int archetype() { return Archetype.UNIVERSAL; }
    /** Логика для одной энтити. Структурные/чужеэнтити эффекты — только через CommandBuffer. */
    void run(View v, int entity, CommandBuffer cb);
}
