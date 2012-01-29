# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
# Copyright 2011 Nebula, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime
import logging
import operator

from django import shortcuts
from django import http
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext as _
from keystoneclient import exceptions as api_exceptions

from horizon import api
from horizon import exceptions
from horizon import forms
from horizon import tables
from .forms import AddUser, CreateTenant, UpdateTenant, UpdateQuotas
from .tables import TenantsTable, TenantUsersTable, AddUsersTable

from horizon.dashboards.syspanel.overview.views import GlobalSummary


LOG = logging.getLogger(__name__)


class IndexView(tables.DataTableView):
    table_class = TenantsTable
    template_name = 'syspanel/tenants/index.html'

    def get_data(self):
        tenants = []
        try:
            tenants = api.tenant_list(self.request)
        except api_exceptions.AuthorizationFailure, e:
            LOG.exception("Unauthorized attempt to list tenants.")
            messages.error(self.request, _('Unable to get tenant info: %s')
                                         % e.message)
        except Exception, e:
            LOG.exception('Exception while getting tenant list')
            if not hasattr(e, 'message'):
                e.message = str(e)
            messages.error(self.request, _('Unable to get tenant info: %s')
                                         % e.message)
        tenants.sort(key=lambda x: x.id, reverse=True)
        return tenants


class CreateView(forms.ModalFormView):
    form_class = CreateTenant
    template_name = 'syspanel/tenants/create.html'


class UpdateView(forms.ModalFormView):
    form_class = UpdateTenant
    template_name = 'syspanel/tenants/update.html'
    context_object_name = 'tenant'

    def get_object(self, *args, **kwargs):
        tenant_id = kwargs['tenant_id']
        try:
            return api.tenant_get(self.request, tenant_id)
        except Exception as e:
            LOG.exception('Error fetching tenant with id "%s"' % tenant_id)
            messages.error(self.request, _('Unable to update tenant: %s')
                                           % e.message)
            raise http.Http404("Tenant with ID %s not found." % tenant_id)

    def get_initial(self):
        return {'id': self.object.id,
                'name': self.object.name,
                'description': getattr(self.object, "description", ""),
                'enabled': self.object.enabled}


class UsersView(tables.MultiTableView):
    table_classes = (TenantUsersTable, AddUsersTable)
    template_name = 'syspanel/tenants/users.html'

    def get_data(self, *args, **kwargs):
        tenant_id = self.kwargs["tenant_id"]
        try:
            self.tenant = api.keystone.tenant_get(self.request, tenant_id)
            self.all_users = api.keystone.user_list(self.request)
            self.tenant_users = api.keystone.user_list(self.request, tenant_id)
        except:
            redirect = reverse("horizon:syspanel:tenants:index")
            exceptions.handle(self.request,
                              _("Unable to retrieve users."),
                              redirect=redirect)
        return super(UsersView, self).get_data(*args, **kwargs)

    def get_tenant_users_data(self):
        return self.tenant_users

    def get_add_users_data(self):
        tenant_user_ids = [user.id for user in self.tenant_users]
        return [user for user in self.all_users if
                user.id not in tenant_user_ids]

    def get_context_data(self, **kwargs):
        context = super(UsersView, self).get_context_data(**kwargs)
        context['tenant'] = self.tenant
        return context


class AddUserView(forms.ModalFormView):
    form_class = AddUser
    template_name = 'syspanel/tenants/add_user.html'
    context_object_name = 'tenant'

    def get_object(self, *args, **kwargs):
        return api.keystone.tenant_get(self.request, kwargs["tenant_id"])

    def get_context_data(self, **kwargs):
        context = super(AddUserView, self).get_context_data(**kwargs)
        context['tenant_id'] = self.kwargs["tenant_id"]
        context['user_id'] = self.kwargs["user_id"]
        return context

    def get_form_kwargs(self):
        kwargs = super(AddUserView, self).get_form_kwargs()
        try:
            roles = api.keystone.role_list(self.request)
        except:
            redirect = reverse("horizon:syspanel:tenants:users",
                               args=(self.kwargs["tenant_id"],))
            exceptions.handle(self.request,
                              _("Unable to retrieve roles."),
                              redirect=redirect)
        roles.sort(key=operator.attrgetter("id"))
        kwargs['roles'] = roles
        return kwargs

    def get_initial(self):
        default_role = api.keystone.get_default_role(self.request)
        return {'tenant_id': self.kwargs['tenant_id'],
                'user_id': self.kwargs['user_id'],
                'role_id': getattr(default_role, "id", None)}


class QuotasView(forms.ModalFormView):
    form_class = UpdateQuotas
    template_name = 'syspanel/tenants/quotas.html'
    context_object_name = 'tenant'

    def get_object(self, *args, **kwargs):
        return api.keystone.tenant_get(self.request, kwargs["tenant_id"])

    def get_initial(self):
        quotas = api.nova.tenant_quota_get(self.request,
                                               self.kwargs['tenant_id'])
        return {
            'tenant_id': self.kwargs['tenant_id'],
            'metadata_items': quotas.metadata_items,
            'injected_file_content_bytes': quotas.injected_file_content_bytes,
            'volumes': quotas.volumes,
            'gigabytes': quotas.gigabytes,
            'ram': int(quotas.ram),
            'floating_ips': quotas.floating_ips,
            'instances': quotas.instances,
            'injected_files': quotas.injected_files,
            'cores': quotas.cores}


def usage(request, tenant_id):
    today = datetime.date.today()
    dateform = forms.DateForm(request.GET, initial={'year': today.year,
                                                    "month": today.month})
    if dateform.is_valid():
        req_year = int(dateform.cleaned_data['year'])
        req_month = int(dateform.cleaned_data['month'])
    else:
        req_year = today.year
        req_month = today.month
    date_start, date_end, datetime_start, datetime_end = \
            GlobalSummary.get_start_and_end_date(req_year, req_month)

    if date_start > GlobalSummary.current_month():
        messages.error(request, _('No data for the selected period'))
        datetime_end = datetime_start

    usage = {}
    try:
        usage = api.usage_get(request, tenant_id, datetime_start, datetime_end)
    except api_exceptions.ApiException, e:
        LOG.exception('ApiException getting usage info for tenant "%s"'
                  ' on date range "%s to %s"' % (tenant_id,
                                                 datetime_start,
                                                 datetime_end))
        messages.error(request, _('Unable to get usage info: %s') % e.message)

    running_instances = []
    terminated_instances = []
    if hasattr(usage, 'server_usages'):
        now = datetime.datetime.now()
        for i in usage.server_usages:
            # this is just a way to phrase uptime in a way that is compatible
            # with the 'timesince' filter.  Use of local time intentional
            i['uptime_at'] = now - datetime.timedelta(seconds=i['uptime'])
            if i['ended_at']:
                terminated_instances.append(i)
            else:
                running_instances.append(i)

    if request.GET.get('format', 'html') == 'csv':
        template = 'syspanel/tenants/usage.csv'
        mimetype = "text/csv"
    else:
        template = 'syspanel/tenants/usage.html'
        mimetype = "text/html"

    context = {'dateform': dateform,
               'datetime_start': datetime_start,
               'datetime_end': datetime_end,
               'global_summary': usage,
               'usage_list': [usage],
               'csv_link': GlobalSummary.csv_link(date_start),
               'instances': running_instances + terminated_instances,
               'tenant_id': tenant_id}

    return shortcuts.render(request, template, context, content_type=mimetype)
