package ecs;

/** Бросается, когда система обращается к необъявленному компоненту. Контракт не проглатывается. */
public final class ContractViolation extends RuntimeException {
    public ContractViolation(String m) { super(m); }
}
