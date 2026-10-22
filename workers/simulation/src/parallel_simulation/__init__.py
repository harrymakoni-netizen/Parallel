"""Engine execution worker (Celery). Start with:

celery -A parallel_simulation.worker worker -Q cpu,maintenance --pool prefork -c 2
celery -A parallel_simulation.worker beat          # recovery sweep schedule
"""
