# -*- coding: utf-8 -*-

# This file is part of wger Workout Manager.
#
# wger Workout Manager is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# wger Workout Manager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# You should have received a copy of the GNU Affero General Public License

# Standard Library
import logging
import uuid

# Django
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin
)
from django.core import mail
from django.core.exceptions import ValidationError
from django.forms import (
    CharField,
    CheckboxSelectMultiple,
    ModelChoiceField,
    ModelForm,
    ModelMultipleChoiceField,
    Select,
    Textarea
)
from django.http import (
    HttpResponseForbidden,
    HttpResponseRedirect
)
from django.shortcuts import (
    get_object_or_404,
    render
)
from django.template.loader import render_to_string
from django.urls import (
    reverse,
    reverse_lazy
)
from django.utils.cache import patch_vary_headers
from django.utils.translation import (
    ugettext as _,
    ugettext_lazy
)
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    UpdateView
)

# Third Party
from crispy_forms.layout import (
    Column,
    Layout,
    Row
)

# wger
from wger.config.models import LanguageConfig
from wger.exercises.models import (
    Exercise,
    ExerciseCategory,
    Muscle
)
from wger.manager.models import WorkoutLog
from wger.utils.constants import MIN_EDIT_DISTANCE_THRESHOLD
from wger.utils.generic_views import (
    WgerDeleteMixin,
    WgerFormMixin
)
from wger.utils.helpers import levenshtein
from wger.utils.language import (
    load_item_languages,
    load_language
)
from wger.utils.widgets import TranslatedSelectMultiple
from wger.weight.helpers import process_log_entries


logger = logging.getLogger(__name__)


class ExerciseListView(ListView):
    """
    Generic view to list all exercises
    """

    model = Exercise
    template_name = 'exercise/overview.html'
    context_object_name = 'exercises'

    def get(self, request, *args, **kwargs):
        response = super(ListView, self).get(request, *args, **kwargs)
        patch_vary_headers(response, ['User-Agent'])
        return response

    def get_queryset(self):
        """
        Filter to only active exercises in the configured languages
        """
        languages = load_item_languages(LanguageConfig.SHOW_ITEM_EXERCISES)
        return Exercise.objects.accepted() \
            .filter(language__in=languages) \
            .order_by('category__id') \
            .select_related()

    def get_context_data(self, **kwargs):
        """
        Pass additional data to the template
        """
        context = super(ExerciseListView, self).get_context_data(**kwargs)
        context['show_shariff'] = True
        return context


def view(request, id, slug=None):
    """
    Detail view for an exercise
    """

    template_data = {}
    template_data['comment_edit'] = False
    template_data['show_shariff'] = True

    exercise = get_object_or_404(Exercise, pk=id)

    template_data['exercise'] = exercise

    template_data["muscles_main_front"] = exercise.muscles.filter(is_front=True)
    template_data["muscles_main_back"] = exercise.muscles.filter(is_front=False)
    template_data["muscles_sec_front"] = exercise.muscles_secondary.filter(is_front=True)
    template_data["muscles_sec_back"] = exercise.muscles_secondary.filter(is_front=False)

    # If the user is logged in, load the log and prepare the entries for
    # rendering in the D3 chart
    entry_log = []
    chart_data = []
    if request.user.is_authenticated:
        logs = WorkoutLog.objects.filter(user=request.user, exercise=exercise)
        entry_log, chart_data = process_log_entries(logs)

    template_data['logs'] = entry_log
    template_data['json'] = chart_data
    template_data['svg_uuid'] = str(uuid.uuid4())
    template_data['cache_vary_on'] = "{}-{}".format(exercise.id, load_language().id)

    return render(request, 'exercise/view.html', template_data)


class ExerciseForm(ModelForm):
    # Redefine some fields here to set some properties
    # (some of this could be done with a crispy form helper and would be
    # a cleaner solution)
    category = ModelChoiceField(queryset=ExerciseCategory.objects.all(),
                                widget=Select())
    muscles = ModelMultipleChoiceField(queryset=Muscle.objects.all(),
                                       widget=CheckboxSelectMultiple(),
                                       required=False)

    muscles_secondary = ModelMultipleChoiceField(queryset=Muscle.objects.all(),
                                                 widget=CheckboxSelectMultiple(),
                                                 required=False)

    description = CharField(label=_('Description'),
                            widget=Textarea,
                            required=False)

    class Meta:
        model = Exercise
        widgets = {'equipment': TranslatedSelectMultiple()}
        fields = ['name_original',
                  'category',
                  'description',
                  'muscles',
                  'muscles_secondary',
                  'equipment',
                  'license',
                  'license_author']

    class Media:
        js = (settings.STATIC_URL + 'yarn/tinymce/tinymce.min.js',)

    def clean_name_original(self):
        """
        Throws a validation error if the submitted name is too similar to an existing
        exercise's name
        """
        name_original = self.cleaned_data['name_original']
        languages = load_item_languages(LanguageConfig.SHOW_ITEM_EXERCISES)
        exercises = Exercise.objects.accepted() \
            .filter(language__in=languages)
        for exercise in exercises:
            exercise_name = str(exercise)
            min_edit_dist = levenshtein(exercise_name.casefold(), name_original.casefold())
            if min_edit_dist < MIN_EDIT_DISTANCE_THRESHOLD:
                raise ValidationError(
                    _('%(name_original)s is too similar to existing exercise "%(exercise_name)s"'),
                    params={'name_original': name_original, 'exercise_name': exercise_name},
                )
        return name_original


class ExercisesEditAddView(WgerFormMixin):
    """
    Generic view to subclass from for exercise adding and editing, since they
    share all this settings
    """
    model = Exercise
    sidebar = 'exercise/form.html'
    title = ugettext_lazy('Add exercise')
    custom_js = 'wgerInitTinymce();'
    clean_html = ('description', )

    def get_form_class(self):
        return ExerciseForm

    def get_form(self, form_class=None):
        form = super(ExercisesEditAddView, self).get_form(form_class)
        form.helper.layout = Layout(
            "name_original",
            "description",
            "category",
            "equipment",
            Row(
                Column('muscles', css_class='form-group col-6 mb-0'),
                Column('muscles_secondary', css_class='form-group col-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('license', css_class='form-group col-6 mb-0'),
                Column('license_author', css_class='form-group col-6 mb-0'),
                css_class='form-row'
            ),
        )
        return form


class ExerciseUpdateView(ExercisesEditAddView,
                         LoginRequiredMixin,
                         PermissionRequiredMixin,
                         UpdateView):
    """
    Generic view to update an existing exercise
    """
    permission_required = 'exercises.change_exercise'

    def get_context_data(self, **kwargs):
        context = super(ExerciseUpdateView, self).get_context_data(**kwargs)
        context['title'] = _('Edit {0}').format(self.object.name)

        return context


class ExerciseAddView(ExercisesEditAddView, LoginRequiredMixin, CreateView):
    """
    Generic view to add a new exercise
    """

    def form_valid(self, form):
        """
        Set language, author and status
        """
        form.instance.language = load_language()
        form.instance.set_author(self.request)
        return super(ExerciseAddView, self).form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        """
        Demo users can't submit exercises
        """
        if request.user.userprofile.is_temporary:
            return HttpResponseForbidden()

        return super(ExerciseAddView, self).dispatch(request, *args, **kwargs)


class ExerciseCorrectView(ExercisesEditAddView, LoginRequiredMixin, UpdateView):
    """
    Generic view to update an existing exercise
    """
    sidebar = 'exercise/form_correct.html'
    messages = _('Thank you. Once the changes are reviewed the exercise will be updated.')

    def dispatch(self, request, *args, **kwargs):
        """
        Only registered users can correct exercises
        """
        if not request.user.is_authenticated or request.user.userprofile.is_temporary:
            return HttpResponseForbidden()

        return super(ExerciseCorrectView, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(ExerciseCorrectView, self).get_context_data(**kwargs)
        context['title'] = _('Correct {0}').format(self.object.name)
        return context

    def form_valid(self, form):
        """
        If the form is valid send email notifications to the site administrators.

        We don't return the super().form_valid because we don't want the data
        to be saved.
        """
        subject = 'Correction submitted for exercise #{0}'.format(self.get_object().pk)
        context = {
            'exercise': self.get_object(),
            'form_data': form.cleaned_data,
            'user': self.request.user
        }
        message = render_to_string('exercise/email_correction.tpl', context)
        mail.mail_admins(str(subject),
                         str(message),
                         fail_silently=True)

        messages.success(self.request, self.messages)
        return HttpResponseRedirect(reverse('exercise:exercise:view',
                                            kwargs={'id': self.object.id}))


class ExerciseDeleteView(WgerDeleteMixin,
                         LoginRequiredMixin,
                         PermissionRequiredMixin,
                         DeleteView):
    """
    Generic view to delete an existing exercise
    """

    model = Exercise
    fields = ('category',
              'description',
              'name_original',
              'muscles',
              'muscles_secondary',
              'equipment')
    success_url = reverse_lazy('exercise:exercise:overview')
    delete_message_extra = ugettext_lazy('This will delete the exercise from all workouts.')
    messages = ugettext_lazy('Successfully deleted')
    permission_required = 'exercises.delete_exercise'

    def get_context_data(self, **kwargs):
        """
        Send some additional data to the template
        """
        context = super(ExerciseDeleteView, self).get_context_data(**kwargs)
        context['title'] = _('Delete {0}?').format(self.object.name)
        return context


class PendingExerciseListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    Generic view to list all weight units
    """

    model = Exercise
    template_name = 'exercise/pending.html'
    context_object_name = 'exercise_list'
    permission_required = 'exercises.change_exercise'

    def get_queryset(self):
        """
        Only show pending exercises
        """
        return Exercise.objects.pending().order_by('-creation_date')


@permission_required('exercises.add_exercise')
def accept(request, pk):
    """
    Accepts a pending user submitted exercise and emails the user, if possible
    """
    exercise = get_object_or_404(Exercise, pk=pk)
    exercise.status = Exercise.STATUS_ACCEPTED
    exercise.save()
    exercise.send_email(request)
    messages.success(request, _('Exercise was successfully added to the general database'))

    return HttpResponseRedirect(exercise.get_absolute_url())


@permission_required('exercises.add_exercise')
def decline(request, pk):
    """
    Declines and deletes a pending user submitted exercise
    """
    exercise = get_object_or_404(Exercise, pk=pk)
    exercise.status = Exercise.STATUS_DECLINED
    exercise.save()
    messages.success(request, _('Exercise was successfully marked as rejected'))
    return HttpResponseRedirect(exercise.get_absolute_url())
