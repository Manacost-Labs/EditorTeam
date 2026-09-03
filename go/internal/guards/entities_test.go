package guards

import "testing"

func TestCompareProtectsLinksNumbersAndNegation(t *testing.T) {
	before := "Не оставляйте карту за 3 маны: https://example.test/a."
	after := "Оставляйте карту за 4 маны: https://example.test/a."
	report := Compare(before, after)
	if len(report.Missing) == 0 || len(report.Changed) == 0 {
		t.Fatalf("затронутые элементы не найдены: %+v", report)
	}
	if !report.HasHardChanges() {
		t.Fatal("изменение защищенного элемента должно быть жестким")
	}
}

func TestCompareAllowsPureProseEdit(t *testing.T) {
	if got := Compare("Карта за 3 маны.", "Эта карта стоит 3 маны."); got.HasHardChanges() {
		t.Fatalf("ложное срабатывание: %+v", got)
	}
}
