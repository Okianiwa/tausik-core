package ecs;

import ecs.scene.BlockEntityScene;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Раскладка по стадиям. Архетип больше не называется руками — система объявляет component-query,
 * и архетип матчится по superset. Поэтому «тот же архетип» / «разные архетипы» выражается
 * ЗАПРОСОМ: F требует RECIPE (есть только у печи), M требует ENERGY (только у машины).
 */
class SchedulerLayoutTest {

    private static GameSystem sys(String name, long reads, long writes, long query) {
        return new GameSystem() {
            public String name() { return name; }
            public long reads()  { return reads; }
            public long writes() { return writes; }
            public long query()  { return query; }
            public void run(View v, int e, CommandBuffer cb) {}
        };
    }

    @Test
    void blockEntitySystemsPackWithFineGranularity() {
        ArchetypeWorld w = BlockEntityScene.build(30_000);
        Scheduler s = new Scheduler(BlockEntityScene.systems(), w);
        // Все трогают INVENTORY, но архетипы disjoint → тонкая гранулярность пакует в 1 стадию.
        assertEquals(1, s.stages.size(), "разные архетипы с общим типом компонента → одна стадия");
        assertEquals(3, s.stages.get(0).length);
    }

    @Test
    void sameArchetypeStillSerializes() {
        ArchetypeWorld w = BlockEntityScene.build(30_000);
        long invW = Components.bit(Components.INVENTORY);
        long furnaceQuery = Components.mask(Components.INVENTORY, Components.RECIPE);
        List<GameSystem> sys = List.of(
                sys("F1", 0, invW, furnaceQuery),
                sys("F2", 0, invW, furnaceQuery)); // тот же архетип + общий write
        assertEquals(2, new Scheduler(sys, w).stages.size(),
                "тот же архетип с пересечением компонентов → разные стадии (гранулярность не дырявая)");
    }

    @Test
    void differentArchetypeSameComponentPacks() {
        ArchetypeWorld w = BlockEntityScene.build(30_000);
        long invW = Components.bit(Components.INVENTORY);
        List<GameSystem> sys = List.of(
                sys("F", 0, invW, Components.mask(Components.INVENTORY, Components.RECIPE)),  // только печь
                sys("M", 0, invW, Components.mask(Components.INVENTORY, Components.ENERGY))); // только машина
        assertEquals(1, new Scheduler(sys, w).stages.size(),
                "разные архетипы не конфликтуют даже по общему write");
    }

    @Test
    void disjointWritesPackIntoOneStage() {
        long mask = Components.mask(Components.PROGRESS, Components.ENERGY, Components.HEAT);
        ArchetypeWorld w = BoundaryTest.world(mask, 10);
        List<GameSystem> sys = List.of(
                sys("A", 0, Components.bit(Components.PROGRESS), Components.bit(Components.PROGRESS)),
                sys("B", 0, Components.bit(Components.ENERGY),   Components.bit(Components.ENERGY)),
                sys("C", 0, Components.bit(Components.HEAT),     Components.bit(Components.HEAT)));
        assertEquals(1, new Scheduler(sys, w).stages.size(), "непересекающиеся writes → одна стадия");
    }

    @Test
    void readReadIsNotConflict() {
        long mask = Components.mask(Components.HEAT, Components.PROGRESS, Components.ENERGY);
        ArchetypeWorld w = BoundaryTest.world(mask, 10);
        long rHeat = Components.bit(Components.HEAT);
        List<GameSystem> sys = List.of(
                sys("R1", rHeat, Components.bit(Components.PROGRESS), mask),
                sys("R2", rHeat, Components.bit(Components.ENERGY),   mask));
        assertEquals(1, new Scheduler(sys, w).stages.size(), "read∩read — не конфликт");
    }

    @Test
    void readWriteOverlapSplitsStages() {
        long mask = Components.mask(Components.HEAT, Components.ENERGY);
        ArchetypeWorld w = BoundaryTest.world(mask, 10);
        List<GameSystem> sys = List.of(
                sys("W", 0, Components.bit(Components.HEAT), mask),
                sys("R", Components.bit(Components.HEAT), Components.bit(Components.ENERGY), mask));
        assertEquals(2, new Scheduler(sys, w).stages.size(), "r∩w на общих строках → разные стадии");
    }

    /** Матч по SUPERSET: лишние компоненты у архетипа матчу не мешают. */
    @Test
    void archetypeMatchesWhenSupersetOfQuery() {
        ArchetypeWorld w = BoundaryTest.world(
                Components.mask(Components.HEAT, Components.ENERGY, Components.PROGRESS), 4);
        List<GameSystem> sys = List.of(
                sys("S", 0, Components.bit(Components.HEAT), Components.bit(Components.HEAT)));
        assertEquals(1, new Scheduler(sys, w).stages.size());
    }
}
