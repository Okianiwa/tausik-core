package ecs;

import ecs.scene.BlockEntityScene;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SchedulerLayoutTest {

    private static GameSystem sys(String name, long reads, long writes) {
        return new GameSystem() {
            public String name() { return name; }
            public long reads()  { return reads; }
            public long writes() { return writes; }
            public void run(View v, int e, CommandBuffer cb) {}
        };
    }

    @Test
    void blockEntitySystemsSerializeOnInventory() {
        Scheduler s = new Scheduler(BlockEntityScene.systems());
        // Печь/машина/хоппер все трогают INVENTORY → консервативная type-гранулярность сериализует их.
        assertEquals(3, s.stages.size(), "ожидаем 3 стадии по 1 системе (полная сериализация)");
        for (int[] stage : s.stages) assertEquals(1, stage.length);
        assertEquals("FurnaceSmelt",   s.systems.get(s.stages.get(0)[0]).name());
        assertEquals("MachineProcess", s.systems.get(s.stages.get(1)[0]).name());
        assertEquals("HopperTransfer", s.systems.get(s.stages.get(2)[0]).name());
    }

    @Test
    void disjointWritesPackIntoOneStage() {
        List<GameSystem> sys = List.of(
                sys("A", 0, Components.bit(Components.PROGRESS)),
                sys("B", 0, Components.bit(Components.ENERGY)),
                sys("C", 0, Components.bit(Components.HEAT)));
        assertEquals(1, new Scheduler(sys).stages.size(), "непересекающиеся writes → одна стадия");
    }

    @Test
    void readReadIsNotConflict() {
        long rHeat = Components.bit(Components.HEAT);
        List<GameSystem> sys = List.of(
                sys("R1", rHeat, Components.bit(Components.PROGRESS)),
                sys("R2", rHeat, Components.bit(Components.ENERGY)));
        assertEquals(1, new Scheduler(sys).stages.size(), "read∩read — не конфликт");
    }

    @Test
    void readWriteOverlapSplitsStages() {
        List<GameSystem> sys = List.of(
                sys("W", 0, Components.bit(Components.HEAT)),
                sys("R", Components.bit(Components.HEAT), Components.bit(Components.ENERGY)));
        assertTrue(new Scheduler(sys).stages.size() == 2, "r∩w → разные стадии");
    }
}
