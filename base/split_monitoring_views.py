"""Authenticated gateway to the separately running, unmodified line-card app."""
import http.client
import json
import logging
import re
from urllib.parse import quote
from django.conf import settings
from django.http import HttpResponse,JsonResponse,Http404
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_exempt
from .access import role_for,ADMIN
from scripts.split_monitoring_runtime import ensure_running,Unavailable

logger=logging.getLogger(__name__)
MAX_RESPONSE=32*1024*1024


def forward(request,path):
    try:
        config=ensure_running(settings.SPLIT_MONITORING_CONFIG)
        conn=http.client.HTTPConnection('127.0.0.1',config['port'],timeout=120 if path=='/card-api/refresh' else 30)
        headers={key:request.headers[key] for key in ('Content-Type','Origin','Sec-Fetch-Site','X-Line-Card-Snapshot') if key in request.headers}
        try:
            target=quote(path,safe='/%')+('?' + request.META.get('QUERY_STRING','') if request.META.get('QUERY_STRING') else '')
            conn.request(request.method,target,body=request.body if request.method=='POST' else None,headers=headers)
            upstream=conn.getresponse();body=upstream.read(MAX_RESPONSE+1)
            if len(body)>MAX_RESPONSE:raise Unavailable('Слишком большой ответ мониторинга.')
            response=HttpResponse(body,status=upstream.status,content_type=upstream.getheader('Content-Type','application/octet-stream'))
            for header in ('Content-Length','Content-Disposition','ETag','Last-Modified'):
                if upstream.getheader(header) is not None:response[header]=upstream.getheader(header)
            response['Cache-Control']='private, no-store';response['X-Content-Type-Options']='nosniff'
            patch_vary_headers(response,('Cookie',))
            return response
        finally:conn.close()
    except (Unavailable,OSError,http.client.HTTPException):
        logger.exception('Split monitoring gateway unavailable')
        if path=='/' and request.method in ('GET','HEAD'):
            return render(request,'base/split_monitoring_unavailable.html',status=503)
        return JsonResponse({'error':'Мониторинг временно недоступен. Обратитесь к администратору портала.'},status=503)


def application(request,path=''):
    if request.method not in ('GET','HEAD'):return HttpResponse(status=405,headers={'Allow':'GET, HEAD'})
    if any(piece in ('..','.') or piece.startswith('.') for piece in path.split('/') if piece) or '\\' in path:raise Http404
    # The upstream only serves its verified app/dist, never code or private data.
    return forward(request,'/'+path)


@csrf_exempt
def api(request,path):
    # The shipped client uses JSON POST without Django CSRF tokens. Require
    # an exact same-origin browser Origin in addition to portal session auth.
    # No request can choose an upstream host or access the worker control API.
    readonly=path in ('health','meta') or bool(re.fullmatch(r'snapshot/[0-9a-f]{64}\.json',path))
    if readonly:
        if request.method not in ('GET','HEAD'):return JsonResponse({'error':'Метод не поддерживается.'},status=405)
    elif path in ('card','refresh'):
        if request.method!='POST':return JsonResponse({'error':'Используйте POST.'},status=405)
        if request.headers.get('Origin')!=request.scheme+'://'+request.get_host() or request.headers.get('Sec-Fetch-Site')=='cross-site':
            return JsonResponse({'error':'Запрос с другого сайта запрещён.'},status=403)
        if path=='refresh' and (role_for(request.user,'split_monitoring')!=ADMIN or getattr(request,'portal_preview_user',None)):
            return JsonResponse({'error':'Обновление из базы доступно администратору мониторинга.'},status=403)
        if not request.content_type.lower().startswith('application/json'):
            return JsonResponse({'error':'Ожидается application/json.'},status=415)
        try:
            if int(request.META.get('CONTENT_LENGTH') or 0)>20000:return JsonResponse({'error':'Запрос слишком велик.'},status=413)
            if len(request.body)>20000:return JsonResponse({'error':'Запрос слишком велик.'},status=413)
            if not isinstance(json.loads(request.body or b'{}'),dict):raise ValueError()
        except (ValueError,UnicodeDecodeError):return JsonResponse({'error':'Некорректный JSON.'},status=400)
    else:raise Http404
    return forward(request,'/card-api/'+path)
