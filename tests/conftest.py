import os

# pas de calcul automatique « de la nuit » pendant les tests (les plateformes de test démarrent un serveur)
os.environ.setdefault("LABO_AUTO_HEURE", "-1")
