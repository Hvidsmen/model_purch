# Раскрытие детализации отчёта мотивации менеджера

В пользовательском RDL «Отчет мотивация менеджера» детализация группы
`ГруппаПлановПродаж` раскрыта по умолчанию только для `VariationCalculate = Подразделение`.
Названия и итоги остальных разделов остаются видимыми; детали раскрываются
через текстовое поле `VariationCalculate` в заголовке родительского раздела.
SQL, параметры, суммы и формулы отчёта не изменены.

В `TablixMember` с группой `ГруппаПлановПродаж` добавлено:

```xml
<Visibility>
  <Hidden>=LCase(Trim(CStr(Fields!VariationCalculate.Value))) &lt;&gt; "подразделение"</Hidden>
  <ToggleItem>VariationCalculate</ToggleItem>
</Visibility>
```

В Textbox `VariationCalculate` добавлено:

```xml
<InitialToggleState>=LCase(Trim(CStr(Fields!VariationCalculate.Value))) = "подразделение"</InitialToggleState>
```

Проверено: XML разбирается; после удаления двух добавленных элементов исходное
дерево полностью совпадает. Отображение и интерактивное раскрытие нужно проверить
в Preview Report Builder/SSRS; доступа к серверу отчётов в этой среде нет.
