from django.core.management.base import BaseCommand
from admin_motivation.services.shareholder_runner import run_calculation


class Command(BaseCommand):
    help='Execute one independent shareholder scenario in a durable process.'
    def add_arguments(self,parser):parser.add_argument('--run',required=True)
    def handle(self,*args,**options):run_calculation(options['run'])
