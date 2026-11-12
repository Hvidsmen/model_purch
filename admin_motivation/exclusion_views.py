"""Shared exclusion reference in the motivation workspace."""
from django import forms
from django.contrib import messages
from django.db import transaction
from django.forms import modelformset_factory
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from base.access import can_access, motivation_editor_required
from .models import MotivationSubdivisionExclusion


class ExclusionForm(forms.ModelForm):
    class Meta:
        model = MotivationSubdivisionExclusion
        fields = ('subdivision_name', 'enabled', 'comment')
        widgets = {
            'subdivision_name': forms.TextInput(attrs={'class': 'form-control'}),
            'comment': forms.TextInput(attrs={'class': 'form-control'}),
        }


@require_http_methods(['GET', 'POST'])
def directory(request):
    motivation_editor_required(request)
    formset_type = modelformset_factory(MotivationSubdivisionExclusion, form=ExclusionForm,
                                       extra=1, max_num=500, validate_max=True)
    formset = formset_type(request.POST if request.method == 'POST' else None,
                          queryset=MotivationSubdivisionExclusion.objects.all(), prefix='exclusions')
    if request.method == 'POST' and formset.is_valid():
        with transaction.atomic():
            formset.save()
        messages.success(request, 'Справочник исключений сохранён. Правила общие для плана и факта всех версий.')
        return redirect('motivation_exclusion_directory')
    return render(request, 'admin_motivation/exclusion_directory.html', {
        'formset': formset, 'can_edit': can_access(request.user, 'motivation_editor'),
        'skip_motivation_workflow': True,
    })
