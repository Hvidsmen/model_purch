from decimal import Decimal
from django import forms
from .models import DirectorEmployee, GlobalDirectorCoeff, SubdivisionDirectorCoeff

KEYS = [f'k{i}' for i in range(5)]


class DirectorEmployeeForm(forms.ModelForm):
    class Meta:
        model = DirectorEmployee
        fields = ['full_name', 'employee_code', 'subdivisions']
        widgets = {'full_name': forms.TextInput(attrs={'class': 'form-control'}),
                   'employee_code': forms.TextInput(attrs={'class': 'form-control'}),
                   'subdivisions': forms.SelectMultiple(attrs={'class': 'form-select'})}

    def clean_employee_code(self):
        return self.cleaned_data['employee_code'].strip()

    def clean_full_name(self):
        return self.cleaned_data['full_name'].strip()


class DirectorPercentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key in KEYS:
            self.fields[key] = forms.DecimalField(label=key.upper() + ', %', min_value=-100, max_value=100,
                max_digits=9, decimal_places=4, widget=forms.NumberInput(attrs={
                    'class': 'form-control form-control-sm', 'step': '0.0001', 'aria-label': key.upper() + ', %'}))
            self.initial[key] = (Decimal(str(getattr(self.instance, key, 0))) * 100).quantize(Decimal('0.0001'))
        if 'goods' in self.fields:
            self.fields['goods'].label = 'Товарная матрица'
            self.fields['goods'].widget.attrs['class'] = 'form-select'

    def clean(self):
        cleaned = super().clean()
        for key in KEYS:
            if key in cleaned:
                cleaned[key] /= Decimal(100)
        return cleaned


class GlobalDirectorForm(DirectorPercentForm):
    class Meta:
        model = GlobalDirectorCoeff
        fields = KEYS


class LocalDirectorForm(DirectorPercentForm):
    class Meta:
        model = SubdivisionDirectorCoeff
        fields = KEYS


class GlobalDirectorAddForm(GlobalDirectorForm):
    class Meta(GlobalDirectorForm.Meta):
        fields = ['goods', *KEYS]


class LocalDirectorAddForm(LocalDirectorForm):
    class Meta(LocalDirectorForm.Meta):
        fields = ['goods', *KEYS]
