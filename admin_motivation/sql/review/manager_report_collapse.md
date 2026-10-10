# Раскрытие детализации отчёта мотивации менеджера

В пользовательском RDL «Отчет мотивация менеджера» детализация группы
`ГруппаПлановПродаж` свёрнута по умолчанию только для `VariationCalculate = Подразделение`.
Остальные разделы раскрыты по умолчанию.
Названия и итоги остальных разделов остаются видимыми; детали можно раскрывать и сворачивать
через текстовое поле `VariationCalculate` в заголовке родительского раздела.
SQL, параметры, суммы и формулы отчёта не изменены.

В `TablixMember` с группой `ГруппаПлановПродаж` добавлено:

```xml
<Visibility>
  <Hidden>=LCase(Trim(CStr(Fields!VariationCalculate.Value))) = "подразделение"</Hidden>
  <ToggleItem>VariationCalculate</ToggleItem>
</Visibility>
```

В Textbox `VariationCalculate` добавлено:

```xml
<ToggleImage>
  <InitialState>=LCase(Trim(CStr(Fields!VariationCalculate.Value))) &lt;&gt; "подразделение"</InitialState>
</ToggleImage>
```

Проверено: XML разбирается; после удаления Visibility и ToggleImage исходное
дерево полностью совпадает. Отображение и интерактивное раскрытие нужно проверить
в Preview Report Builder/SSRS; доступа к серверу отчётов в этой среде нет.

## Оформление

Подготовлен вариант `manager_motivation_redesigned.rdl`: Segoe UI, тёмно-синий
заголовок, светлые серо-синие шапки, выделенные итоги, тонкие светлые границы.
Размеры отчёта, SQL, тексты, числовые выражения, условные цвета показателей
и раскрытие разделов сохранены. Сравнение XML подтвердило неизменность
Value, CommandText, GroupExpression, Hidden, ToggleItem и InitialState.
Визуальную проверку необходимо выполнить в Report Builder Preview.
