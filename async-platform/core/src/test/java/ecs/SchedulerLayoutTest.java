package ecs;

import ecs.scene.BlockEntityScene;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

class SchedulerLayoutTest {

    private static GameSystem sys(String name, long reads, long writes, int arch) {
        return new GameSystem() {
            public String name() { return name; }
            public int archetype() { return arch; }
            public long reads()  { return reads; }
            public long writes() { return writes; }
            public void run(View v, int e, CommandBuffer cb) {}
        };
    }

    @Test
    void blockEntitySystemsPackWithFineGranularity() {
        World w = BlockEntityScene.build(30_000);
        Scheduler s = new Scheduler(BlockEntityScene.systems(), w);
        // Все трогают INVENTORY, но архетипы disjoint → тонкая гранулярность пакует в 1 стадию.
        assertEquals(1, s.stages.size(), "разные архетипы с общим типом компонента → одна стадия");
        assertEquals(3, s.stages.get(0).length);
    }

    @Test
    void sameArchetypeStillSerializes() {
        World w = BlockEntityScene.build(30_000); // FURNACE-диапазон непустой
        long invW = Components.bit(Components.INVENTORY);
        List<GameSystem> sys = List.of(
                sys("F1", 0, invW, Archetype.FURNACE),
                sys("F2", 0, invW, Archetype.FURNACE)); // тот же архетип + общий write
        assertEquals(2, new Scheduler(sys, w).stages.size(),
                "тот же архетип с пересечением компонентов → разные стадии (гранулярность не дырявая)");
    }

    @Test
    void differentArchetypeSameComponentPacks() {
        World w = BlockEntityScene.build(30_000);
        long invW = Components.bit(Components.INVENTORY);
        List<GameSystem> sys = List.of(
                sys("F", 0, invW, Archetype.FURNACE),
                sys("M", 0, invW, Archetype.MACHINE)); // общий write, но disjoint строки
        assertEquals(1, new Scheduler(sys, w).stages.size(),
                "разные архетипы (disjoint строки) не конфликтуют даже по общему write");
    }

    @Test
    void disjointWritesPackIntoOneStage() {
        World w = new World(10);
        List<GameSystem> sys = List.of(
                sys("A", 0, Components.bit(Components.PROGRESS), Archetype.UNIVERSAL),
                sys("B", 0, Components.bit(Components.ENERGY),   Archetype.UNIVERSAL),
                sys("C", 0, Components.bit(Components.HEAT),     Archetype.UNIVERSAL));
        assertEquals(1, new Scheduler(sys, w).stages.size(), "непересекающиеся writes → одна стадия");
    }

    @Test
    void readReadIsNotConflict() {
        World w = new World(10);
        long rHeat = Components.bit(Components.HEAT);
        List<GameSystem> sys = List.of(
                sys("R1", rHeat, Components.bit(Components.PROGRESS), Archetype.UNIVERSAL),
                sys("R2", rHeat, Components.bit(Components.ENERGY),   Archetype.UNIVERSAL));
        assertEquals(1, new Scheduler(sys, w).stages.size(), "read∩read — не конфликт");
    }

    @Test
    void readWriteOverlapSplitsStages() {
        World w = new World(10);
        List<GameSystem> sys = List.of(
                sys("W", 0, Components.bit(Components.HEAT), Archetype.UNIVERSAL),
                sys("R", Components.bit(Components.HEAT), Components.bit(Components.ENERGY), Archetype.UNIVERSAL));
        assertEquals(2, new Scheduler(sys, w).stages.size(), "r∩w на общих строках → разные стадии");
    }
}
