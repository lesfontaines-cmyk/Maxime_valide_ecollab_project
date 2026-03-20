#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur Validation Manager — Charles Murgat
Lance: python server.py
"""

import json
import time
import datetime
import os
import re as _re
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─── UTILS ───────────────────────────────────────────────────────────────────
def to_minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m

def min_to_hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


# ─── SETUP SELENIUM (shared) ─────────────────────────────────────────────────
def _make_driver():
    """Lance Chrome headless et retourne le driver."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    import shutil

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    chromium_path = os.environ.get("CHROME_BIN") or \
                    shutil.which("chromium") or \
                    shutil.which("chromium-browser") or \
                    shutil.which("google-chrome")
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH") or \
                        shutil.which("chromedriver")

    if chromium_path:
        opts.binary_location = chromium_path
    if chromedriver_path:
        service = Service(chromedriver_path)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)

    driver.set_page_load_timeout(20)
    driver.set_script_timeout(15)
    return driver


def _login_if_needed(driver, email, password, url):
    """Login sur eCollab si redirigé vers la page de connexion."""
    current = driver.current_url.lower()
    if 'login' in current or 'account' in current or 'connect' in current or 'auth' in current:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By

        email_el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "input[type='email'], input[placeholder='Email'], input[name='Email']"))
        )
        email_el.clear()
        email_el.send_keys(email)
        pwd_el = driver.find_element('css selector', "input[type='password']")
        pwd_el.clear()
        pwd_el.send_keys(password)
        driver.find_element('css selector', "button[type='submit']").click()
        time.sleep(4)

        base_url = '/'.join(url.split('/')[:3])
        driver.add_cookie({'name': 'alert-rgpd', 'value': 'true',
                           'domain': base_url.replace('https://', '').replace('http://', '')})
        driver.get(url)
        time.sleep(3)


def _navigate_ecol(driver, email, password, url, mois, annee):
    """Navigue vers l'URL eCollab du manager avec cookie RGPD et login."""
    url = _re.sub(r'mois=\d+', f'mois={mois:02d}', url)
    url = _re.sub(r'annee=\d+', f'annee={annee}', url)

    base_url = '/'.join(url.split('/')[:3])
    driver.get(base_url)
    time.sleep(1)
    driver.add_cookie({'name': 'alert-rgpd', 'value': 'true',
                       'domain': base_url.replace('https://', '').replace('http://', '')})

    driver.get(url)
    time.sleep(3)
    _login_if_needed(driver, email, password, url)
    time.sleep(3)
    print(f"  [nav] Page: {driver.current_url}", flush=True)
    return url


# ─── FETCH COLLABORATEURS ───────────────────────────────────────────────────
def fetch_collaborateurs(email, password, url):
    """
    Récupère la liste des collaborateurs depuis la page eCollab du manager.
    Retourne (True, [{id, nom, prenom}]) ou (False, erreur).
    """
    driver = None
    try:
        driver = _make_driver()
        today = datetime.date.today()
        url_now = _re.sub(r'mois=\d+', f'mois={today.month:02d}', url)
        url_now = _re.sub(r'annee=\d+', f'annee={today.year}', url_now)

        # Naviguer sur la page du manager (pas SaisieRapide)
        base_url = '/'.join(url_now.split('/')[:3])
        driver.get(base_url)
        time.sleep(1)
        driver.add_cookie({'name': 'alert-rgpd', 'value': 'true',
                           'domain': base_url.replace('https://', '').replace('http://', '')})
        driver.get(url_now)
        time.sleep(3)
        _login_if_needed(driver, email, password, url_now)
        time.sleep(3)
        print(f"  [collabs] Page chargée: {driver.current_url}", flush=True)

        # Extraire la liste : select (idContrat fiable) + Vue model (prenom/nom séparés)
        result = driver.execute_script("""
            // 1) Select du DOM pour les idContrat fiables
            var selectList = [];
            var selects = document.querySelectorAll('select');
            for (var s = 0; s < selects.length; s++) {
                if (selects[s].id === 'sl-mois' || selects[s].id === 'sl-annee' ||
                    selects[s].name === 'mois' || selects[s].name === 'annee') continue;
                var opts = selects[s].querySelectorAll('option');
                if (opts.length > 1) {
                    for (var o = 0; o < opts.length; o++) {
                        var val = opts[o].value;
                        var text = opts[o].textContent.trim();
                        if (val && text && text !== '--') {
                            selectList.push({id: parseInt(val) || val, fullName: text});
                        }
                    }
                    break;
                }
            }

            // 2) Vue model pour prenom/nom séparés
            var vueMap = {};
            try {
                var vueEl = document.querySelector('#variable-paie') ||
                            document.querySelector('#vueSaisieRapide');
                if (!vueEl || !vueEl.__vue__) {
                    var allEls = document.querySelectorAll('*');
                    for (var i = 0; i < allEls.length; i++) {
                        try {
                            var v = allEls[i].__vue__;
                            if (v && v.$data && Object.keys(v.$data).length > 5) {
                                vueEl = allEls[i]; break;
                            }
                        } catch(e) {}
                    }
                }
                if (vueEl && vueEl.__vue__) {
                    var vm = vueEl.__vue__;
                    var src = vm.salaries || vm.listeSalaries || vm.ListeSalaries ||
                              (vm.$data ? (vm.$data.salaries || vm.$data.listeSalaries || vm.$data.ListeSalaries) : null);
                    if (src) {
                        var items = Array.isArray(src) ? src : Object.values(src);
                        for (var i = 0; i < items.length; i++) {
                            var s = items[i];
                            if (!s || typeof s !== 'object') continue;
                            var nom = s.Nom || s.nom || '';
                            var prenom = s.Prenom || s.prenom || '';
                            if (nom || prenom) {
                                var key = ((prenom + ' ' + nom).trim()).toUpperCase();
                                vueMap[key] = {nom: nom, prenom: prenom};
                            }
                        }
                    }
                }
            } catch(e) {}

            // 3) Fusionner : select ids + Vue prenom/nom
            if (selectList.length > 0) {
                for (var i = 0; i < selectList.length; i++) {
                    var match = vueMap[selectList[i].fullName.toUpperCase()];
                    if (match) {
                        selectList[i].nom = match.nom;
                        selectList[i].prenom = match.prenom;
                    }
                }
                return {success: true, collaborateurs: selectList, source: 'select+vue'};
            }

            return {error: 'ERR_NO_DATA'};
        """)

        driver.quit()

        if not result or result.get('error'):
            return False, f"Erreur lecture Vue : {result.get('error', 'inconnu')} — {result.get('keys', '')}"

        return True, result.get('collaborateurs', [])

    except Exception as e:
        if driver:
            try: driver.quit()
            except: pass
        return False, f"Erreur inattendue : {e}"


# ─── JS SCRAPING SCRIPT (shared) ────────────────────────────────────────────
_SCRAPE_JS = """
    var params = new URLSearchParams(window.location.search);
    var annee = params.get('annee') || new Date().getFullYear();
    var moisNum = parseInt(params.get('mois')) || (new Date().getMonth() + 1);
    var moisStr = String(moisNum).padStart(2, '0');

    // --- Trouver l'instance Vue (priorité #vueSaisieRapide comme le projet source) ---
    var vueEl = document.querySelector('#vueSaisieRapide') || document.querySelector('#variable-paie');
    if (!vueEl || !vueEl.__vue__) {
        var divs = document.querySelectorAll('div');
        for (var di = 0; di < divs.length; di++) {
            try {
                var v = divs[di].__vue__;
                if (v && (v.taches || v.TachesDisponibles || v.TachesDispos ||
                    (v.$data && Object.keys(v.$data).length > 5))) {
                    vueEl = divs[di]; break;
                }
            } catch(e) {}
        }
    }
    var vm = (vueEl && vueEl.__vue__) ? vueEl.__vue__ : null;

    var nomSalarie = '';
    try {
        if (vm && vm.$data && vm.$data.model) nomSalarie = vm.$data.model.NomSalarie || '';
    } catch(e) {}

    // --- Construire la map taches id->nom (une seule fois, pas par jour) ---
    var tachesMap = {};
    var _debugTaches = {};
    try {
        var tSrc = null;
        if (vm) {
            tSrc = vm.taches || vm.TachesDisponibles || vm.TachesDispos ||
                   (vm.$data ? (vm.$data.taches || vm.$data.Taches || vm.$data.listeTaches || vm.$data.TachesDisponibles) : null) ||
                   (vm.$root ? (vm.$root.taches || vm.$root.TachesDisponibles || vm.$root.TachesDispos) : null);
        }
        if (tSrc) {
            var tItems = Array.isArray(tSrc) ? tSrc : Object.values(tSrc);
            _debugTaches.tachesCount = tItems.length;
            if (tItems.length > 0 && tItems[0]) {
                _debugTaches.sampleKeys = Object.keys(tItems[0]).join(',');
                _debugTaches.sample = JSON.parse(JSON.stringify(tItems[0]));
            }
            for (var ti = 0; ti < tItems.length; ti++) {
                var tItem = tItems[ti];
                if (!tItem || typeof tItem !== 'object') continue;
                var tid = tItem.Id || tItem.id || tItem.IdTache;
                var tnom = tItem.Libelle || tItem.libelle || tItem.Label || tItem.label ||
                           tItem.Nom || tItem.nom || tItem.Designation || tItem.designation;
                if (tid && tnom) tachesMap[tid] = tnom;
            }
        } else {
            _debugTaches.noTaches = true;
            if (vm) _debugTaches.dataKeys = Object.keys(vm.$data || {}).join(',');
        }
    } catch(e) { _debugTaches.error = e.message; }
    _debugTaches.tachesMapSize = Object.keys(tachesMap).length;

    // --- Préparer le Modele Vue pour fallback plages + tâches ---
    var modele = null;
    try {
        if (vm && vm.$data && vm.$data.model) modele = vm.$data.model.Modele;
    } catch(e) {}

    // --- Scraper le tableau DOM ---
    var rows = document.querySelectorAll('tr');
    var days = {};

    for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].querySelectorAll('td');
        if (cells.length < 5) continue;

        var nameText = cells[0].textContent.trim().replace(/\\s+/g, ' ');
        var dateMatch = nameText.match(/(\\d{2})\\/(\\d{2})/);
        if (!dateMatch) continue;
        var jour = dateMatch[1];
        var moisCell = dateMatch[2];
        if (parseInt(moisCell) !== moisNum) continue;

        var dateKey = annee + '-' + moisStr + '-' + jour;

        var plages = [];
        var dayTachesDOM = [];
        var tippyDivs = cells[1] ? cells[1].querySelectorAll('div[data-tippy-content]') : [];
        for (var t = 0; t < tippyDivs.length; t++) {
            var tip = tippyDivs[t].getAttribute('data-tippy-content') || '';
            var plageMatch = tip.match(/(\\d{2}:\\d{2})-(\\d{2}:\\d{2})/);
            var plageText = tippyDivs[t].textContent.trim();
            if (plageMatch) {
                var tache = (plageText && plageText.length < 80) ? plageText : null;
                plages.push({debut: plageMatch[1], fin: plageMatch[2], tache: tache});
            }
            if (plageText && plageText.length < 80 && dayTachesDOM.indexOf(plageText) === -1) {
                dayTachesDOM.push(plageText);
            }
        }

        var heuresText = cells[3] ? cells[3].textContent.trim() : '0';
        var heuresDecimal = parseFloat(heuresText) || 0;
        var totalMin = Math.round(heuresDecimal * 60);

        var variables = cells[4] ? cells[4].textContent.trim() : '';

        var icon = cells[5] ? cells[5].querySelector('i[data-tippy-content]') : null;
        var tippy = icon ? icon.getAttribute('data-tippy-content') || '' : '';
        var isSuccess = icon ? icon.className.indexOf('text-success') > -1 : false;
        var valideEntreprise = tippy.indexOf('entreprise') > -1 && isSuccess;
        var valideSalarie = tippy.indexOf('salari') > -1 || valideEntreprise;

        var rowText = rows[r].textContent;
        var variable = '';
        if (rowText.indexOf('Cong') > -1) variable = 'Conge paye';
        if (rowText.indexOf('RTT') > -1) variable = 'RTT';
        if (rowText.indexOf('Maladie') > -1) variable = 'Maladie';
        if (rowText.indexOf('Absence') > -1) variable = 'Absence';
        for (var c = 0; c < cells.length; c++) {
            var ct = cells[c].textContent.trim();
            if (ct.length < 80) {
                if (ct.indexOf('Cong') > -1) { variable = ct; break; }
                if (ct.indexOf('RTT') > -1) { variable = ct; break; }
                if (ct.indexOf('Absence') > -1) { variable = ct; break; }
                if (ct.indexOf('Maladie') > -1) { variable = ct; break; }
            }
        }

        // Détection congé/absence uniquement via texte DOM (le Vue model n'a pas IdVariable)
        var varLower = (variable || '').toLowerCase();
        var isAbsenceDay = varLower.indexOf('cong') > -1 || varLower.indexOf('rtt') > -1 ||
                           varLower.indexOf('maladie') > -1 || varLower.indexOf('absence') > -1;

        if (isAbsenceDay && plages.length > 0) {
            // Chercher les plages qui ont un texte d'absence dans le tippy
            var absenceSlots = {};
            var hasAnyAbsencePlage = false;
            for (var pa = 0; pa < plages.length; pa++) {
                var ptxt = (plages[pa].tache || '').toLowerCase();
                if (ptxt.indexOf('cong') > -1 || ptxt.indexOf('absence') > -1 ||
                    ptxt.indexOf('maladie') > -1 || ptxt.indexOf('rtt') > -1) {
                    absenceSlots[plages[pa].debut + '-' + plages[pa].fin] = true;
                    hasAnyAbsencePlage = true;
                }
            }
            if (!hasAnyAbsencePlage) {
                // Plein jour absence (aucune plage tippy n'a de texte absence) → tout vider
                plages = [];
            } else {
                // Demi-journée : garder plages travaillées + plages absence (marquées)
                var filteredPlages = [];
                for (var pw = 0; pw < plages.length; pw++) {
                    var slotKey = plages[pw].debut + '-' + plages[pw].fin;
                    var ptxt2 = (plages[pw].tache || '').toLowerCase();
                    var isAbsPlage = ptxt2.indexOf('cong') > -1 || ptxt2.indexOf('absence') > -1 ||
                                     ptxt2.indexOf('maladie') > -1 || ptxt2.indexOf('rtt') > -1;
                    if (isAbsPlage) {
                        // Garder la plage absence avec marqueur
                        plages[pw].absence = true;
                        filteredPlages.push(plages[pw]);
                    } else if (absenceSlots[slotKey]) {
                        continue; // doublon template du même créneau → skip
                    } else {
                        filteredPlages.push(plages[pw]); // plage travaillée
                    }
                }
                plages = filteredPlages;
            }
        }

        var estTravaille = heuresDecimal > 0 || plages.length > 0;

        // --- Tâches et fallback plages depuis Vue model ---
        var dayTaches = [];
        if (modele) {
            for (var mk in modele) {
                var mj = modele[mk];
                if (mj && mj.Mois === moisNum && mj.Jour === parseInt(jour)) {
                    // Fallback plages depuis Vue si DOM vide ET heures > 0 ET pas un congé
                    if (plages.length === 0 && heuresDecimal > 0 && !variable && mj.Horaires && mj.Horaires.length > 0) {
                        for (var h = 0; h < mj.Horaires.length; h++) {
                            var hr = mj.Horaires[h];
                            if (hr.HeureDebut > 0 || hr.HeureFin > 0) {
                                plages.push({
                                    debut: String(Math.floor(hr.HeureDebut/60)).padStart(2,'0') + ':' + String(hr.HeureDebut%60).padStart(2,'0'),
                                    fin: String(Math.floor(hr.HeureFin/60)).padStart(2,'0') + ':' + String(hr.HeureFin%60).padStart(2,'0')
                                });
                            }
                        }
                    }
                    // Tâches: d'abord Matin/ApresMidi (principal), puis Horaires (fallback)
                    if (mj.Matin && mj.Matin.IdTache) {
                        var nomTm = tachesMap[mj.Matin.IdTache] || ('Tache #' + mj.Matin.IdTache);
                        if (dayTaches.indexOf(nomTm) === -1) dayTaches.push(nomTm);
                    }
                    if (mj.ApresMidi && mj.ApresMidi.IdTache) {
                        var nomTa = tachesMap[mj.ApresMidi.IdTache] || ('Tache #' + mj.ApresMidi.IdTache);
                        if (dayTaches.indexOf(nomTa) === -1) dayTaches.push(nomTa);
                    }
                    // Aussi vérifier IdTache directement sur le jour
                    if (mj.IdTache) {
                        var nomTd = tachesMap[mj.IdTache] || ('Tache #' + mj.IdTache);
                        if (dayTaches.indexOf(nomTd) === -1) dayTaches.push(nomTd);
                    }
                    // Fallback Horaires (souvent null mais on vérifie)
                    if (mj.Horaires) {
                        for (var h2 = 0; h2 < mj.Horaires.length; h2++) {
                            var idT = mj.Horaires[h2].IdTache;
                            if (idT) {
                                var nomT = tachesMap[idT] || ('Tache #' + idT);
                                if (dayTaches.indexOf(nomT) === -1) dayTaches.push(nomT);
                            }
                        }
                    }
                    break;
                }
            }
        }

        // Tâches: priorité DOM (texte des plages), puis Vue model
        var finalTaches = dayTachesDOM.length > 0 ? dayTachesDOM : (dayTaches.length > 0 ? dayTaches : null);

        days[dateKey] = {
            plages: plages,
            travaille: estTravaille,
            valideSalarie: valideSalarie,
            valideEntreprise: valideEntreprise,
            totalHeures: totalMin,
            variable: variable || null,
            variables: variables || null,
            taches: finalTaches
        };
    }

    // Debug: chercher tous les jours avec une tâche non-null
    var _daysWithTache = [];
    if (modele) {
        for (var _dk in modele) {
            var _dj = modele[_dk];
            if (!_dj || _dj.Mois !== moisNum) continue;
            var _mt = _dj.Matin ? _dj.Matin.IdTache : null;
            var _at = _dj.ApresMidi ? _dj.ApresMidi.IdTache : null;
            var _dt = _dj.IdTache || null;
            if (_mt || _at || _dt) {
                _daysWithTache.push({jour: _dj.Jour, matin: _mt, apresmidi: _at, direct: _dt});
            }
            // Aussi dumper Matin complet du premier jour pour debug
            if (!_debugTaches.sampleMatin && _dj.Matin) {
                _debugTaches.sampleMatin = JSON.parse(JSON.stringify(_dj.Matin));
                _debugTaches.sampleApresMidi = _dj.ApresMidi ? JSON.parse(JSON.stringify(_dj.ApresMidi)) : null;
                _debugTaches.sampleDayKeys = Object.keys(_dj).join(',');
            }
        }
    }
    _debugTaches.daysWithTache = _daysWithTache;

    return {success: true, days: days, nomSalarie: nomSalarie, _debugTaches: _debugTaches};
"""


# ─── SESSION CHROME PARTAGÉE ────────────────────────────────────────────────
_shared_driver = None
_shared_driver_expiry = 0


def _get_shared_driver():
    """Retourne le driver partagé s'il est encore valide, sinon None."""
    global _shared_driver, _shared_driver_expiry
    if _shared_driver and time.time() < _shared_driver_expiry:
        try:
            _shared_driver.current_url  # test si vivant
            return _shared_driver
        except Exception:
            try: _shared_driver.quit()
            except: pass
            _shared_driver = None
    return None


def _set_shared_driver(driver):
    """Stocke le driver comme session partagée (TTL 5 min)."""
    global _shared_driver, _shared_driver_expiry
    _shared_driver = driver
    _shared_driver_expiry = time.time() + 300


def close_shared_driver():
    """Ferme la session partagée."""
    global _shared_driver, _shared_driver_expiry
    if _shared_driver:
        try: _shared_driver.quit()
        except: pass
        _shared_driver = None
        _shared_driver_expiry = 0


# ─── FETCH MOIS COLLABORATEUR ───────────────────────────────────────────────
def fetch_mois_collaborateur(email, password, url, salarie_id, mois, annee):
    """Charge le mois d'un collaborateur (réutilise la session Chrome)."""
    try:
        url_collab = _re.sub(r'idContrat=\d+', f'idContrat={salarie_id}', url)
        url_collab = _re.sub(r'mois=\d+', f'mois={int(mois):02d}', url_collab)
        url_collab = _re.sub(r'annee=\d+', f'annee={int(annee)}', url_collab)

        driver = _get_shared_driver()
        if driver:
            # Navigation rapide (même session, pas de re-login)
            print(f"  [shared] Navigation rapide vers: idContrat={salarie_id}", flush=True)
            driver.get(url_collab)
            time.sleep(3)
            _set_shared_driver(driver)  # prolonger TTL
        else:
            # Nouvelle session Chrome + login complet
            print(f"  [shared] Nouvelle session Chrome + login pour idContrat={salarie_id}", flush=True)
            driver = _make_driver()
            _navigate_ecol(driver, email, password, url_collab, int(mois), int(annee))
            _set_shared_driver(driver)

        result = driver.execute_script(_SCRAPE_JS)

        if not result or result.get('error'):
            return False, f"Erreur lecture Vue : {result.get('error', 'inconnu')}"
        return True, result

    except Exception as e:
        close_shared_driver()
        return False, f"Erreur inattendue : {e}"


def fetch_all_mois(email, password, url, salarie_ids, mois, annee):
    """Charge le mois de TOUS les collaborateurs en une seule session Chrome."""
    driver = None
    results = {}
    try:
        driver = _make_driver()

        # Premier collaborateur: navigation complète avec login
        first_id = salarie_ids[0]
        url_collab = _re.sub(r'idContrat=\d+', f'idContrat={first_id}', url)
        print(f"  [all] Login + navigation vers: idContrat={first_id}", flush=True)
        final_url = _navigate_ecol(driver, email, password, url_collab, mois, annee)

        result = driver.execute_script(_SCRAPE_JS)
        if result and result.get('success'):
            results[str(first_id)] = result

        # Collaborateurs suivants: juste changer l'URL (même session, pas de re-login)
        for sid in salarie_ids[1:]:
            try:
                url_next = _re.sub(r'idContrat=\d+', f'idContrat={sid}', final_url)
                print(f"  [all] Navigation rapide vers: idContrat={sid}", flush=True)
                driver.get(url_next)
                time.sleep(3)

                result = driver.execute_script(_SCRAPE_JS)
                if result and result.get('success'):
                    results[str(sid)] = result
            except Exception as e:
                print(f"  [all] Erreur pour {sid}: {e}", flush=True)

        driver.quit()
        return True, results

    except Exception as e:
        if driver:
            try: driver.quit()
            except: pass
        return False, f"Erreur inattendue : {e}"


# ─── VALIDATION JOURS ───────────────────────────────────────────────────────
def valider_jours_selenium(email, password, url, salarie_id, dates, mois, annee):
    """
    Valide les jours en ouvrant la modale de chaque jour,
    cliquant le bouton vert 'Valider et bloquer', fermant la modale,
    puis cliquant Sauvegarder.
    """
    driver = None
    try:
        close_shared_driver()

        driver = _make_driver()
        url_collab = _re.sub(r'idContrat=\d+', f'idContrat={salarie_id}', url)
        print(f"  [valider] Navigation vers: idContrat={salarie_id}", flush=True)
        _navigate_ecol(driver, email, password, url_collab, int(mois), int(annee))

        # Passer en vue "AU MOIS"
        driver.execute_script("""
            var btns = document.querySelectorAll('button, a, div');
            for (var i = 0; i < btns.length; i++) {
                var t = btns[i].textContent.trim().toUpperCase();
                if (t === 'AU MOIS') { btns[i].click(); break; }
            }
        """)
        time.sleep(2)

        validated = []
        dates_jours = [int(d.split('-')[2]) for d in dates]
        print(f"  [valider] Jours à valider: {dates_jours}", flush=True)

        for jour_num in dates_jours:
            jour_str = f"{jour_num:02d}/{int(mois):02d}"
            print(f"  [valider] Ouverture modale jour {jour_str}...", flush=True)

            # Cliquer sur la plage du jour pour ouvrir la modale
            opened = driver.execute_script(f"""
                var rows = document.querySelectorAll('tr');
                for (var r = 0; r < rows.length; r++) {{
                    var cells = rows[r].querySelectorAll('td');
                    if (cells.length < 2) continue;
                    var txt = cells[0].textContent.trim();
                    if (txt.indexOf('{jour_str}') === -1) continue;
                    var plageDiv = rows[r].querySelector('div[data-tippy-content]');
                    if (plageDiv) {{ plageDiv.click(); return 'CLICKED_PLAGE'; }}
                    if (cells[1]) {{ cells[1].click(); return 'CLICKED_CELL'; }}
                }}
                return 'NOT_FOUND';
            """)

            if not opened or opened == 'NOT_FOUND':
                print(f"  [valider] Jour {jour_str} non trouvé", flush=True)
                continue

            time.sleep(1.5)

            # Cliquer le bouton vert "Valider et bloquer la journée"
            result = driver.execute_script("""
                var modal = document.querySelector('.modal.show');
                if (!modal) return 'NO_MODAL';
                var btn = modal.querySelector('button.btn-success.btn-xs');
                if (btn) { btn.click(); return 'CLICKED_BTN_SUCCESS'; }
                return 'NO_VALID_BTN';
            """)
            print(f"  [valider] Bouton entreprise: {result}", flush=True)
            time.sleep(1)

            # Fermer la modale via le bouton "Fermer"
            driver.execute_script("""
                var modal = document.querySelector('.modal.show');
                if (!modal) return;
                var btns = modal.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].textContent.trim().indexOf('Fermer') > -1) {
                        btns[i].click(); return;
                    }
                }
                var closeBtn = modal.querySelector('button.close');
                if (closeBtn) closeBtn.click();
            """)
            time.sleep(1.5)

            if result and result == 'CLICKED_BTN_SUCCESS':
                validated.append(jour_num)

        if len(validated) == 0:
            driver.quit()
            return False, "Aucun jour n'a pu être validé"

        # Attendre que la modale soit bien fermée
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        # Cliquer le bouton Sauvegarder exactement comme dans le navigateur
        save_result = driver.execute_script("""
            var btns = document.querySelectorAll('button.btn-primary');
            for (var i = 0; i < btns.length; i++) {
                if (btns[i].textContent.trim() === 'Sauvegarder') {
                    btns[i].click();
                    return 'CLICKED_SAVE';
                }
            }
            return 'NO_SAVE_BTN';
        """)
        print(f"  [valider] Sauvegarder: {save_result}", flush=True)

        # Attendre que le POST SaveVariablePaie soit terminé (jusqu'à 20s)
        for _wait in range(20):
            time.sleep(1)
            try:
                done = driver.execute_script("""
                    var vm = document.querySelector('#variable-paie');
                    if (vm && vm.__vue__ && vm.__vue__.$data.model) {
                        return !vm.__vue__.$data.model.SauvegardeEnCours;
                    }
                    return true;
                """)
                if done and _wait > 0:
                    print(f"  [valider] Sauvegarde terminée après {_wait+1}s", flush=True)
                    break
            except:
                break
        time.sleep(2)

        print(f"  [valider] Jours validés: {validated}", flush=True)

        driver.quit()
        return True, f"Validation réussie — {len(validated)} jour(s) validé(s)"

    except Exception as e:
        if driver:
            try: driver.quit()
            except: pass
        return False, f"Erreur inattendue : {e}"


# ─── ROUTES API ──────────────────────────────────────────────────────────────

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Serveur Validation Manager opérationnel"})


@app.route("/pre-login", methods=["POST"])
def pre_login():
    """Ouvre une session Chrome partagée avec login, sans charger de collaborateur."""
    data = request.get_json(force=True)
    email    = (data.get("email")    or "").strip()
    password = (data.get("password") or "").strip()
    url      = (data.get("url")      or "").strip()

    if not email or not password or not url:
        return jsonify({"success": False, "error": "Email, mot de passe et URL requis"}), 400

    # Si session partagée déjà active, pas besoin de re-login
    existing = _get_shared_driver()
    if existing:
        return jsonify({"success": True, "message": "Session deja active"})

    try:
        driver = _make_driver()
        today_d = datetime.date.today()
        nav_url = _re.sub(r'mois=\d+', f'mois={today_d.month:02d}', url)
        nav_url = _re.sub(r'annee=\d+', f'annee={today_d.year}', nav_url)
        _navigate_ecol(driver, email, password, nav_url, today_d.month, today_d.year)
        _set_shared_driver(driver)
        print("  [pre-login] Session partagee creee avec succes", flush=True)
        return jsonify({"success": True, "message": "Connexion reussie"})
    except Exception as e:
        msg = str(e).split('\n')[0]
        return jsonify({"success": False, "error": f"Erreur connexion : {msg}"}), 500


@app.route("/test-login", methods=["POST"])
def test_login():
    """Teste les identifiants eCollab via Selenium."""
    data = request.get_json(force=True)
    email    = (data.get("email")    or "").strip()
    password = (data.get("password") or "").strip()
    url      = (data.get("url")      or "").strip()

    if not email or not password:
        return jsonify({"success": False, "error": "Email et mot de passe requis"}), 400
    if not url:
        return jsonify({"success": False, "error": "URL Ecollaboratrice requise"}), 400

    try:
        driver = _make_driver()
        today_d = datetime.date.today()
        url = _re.sub(r'mois=\d+', f'mois={today_d.month:02d}', url)
        url = _re.sub(r'annee=\d+', f'annee={today_d.year}', url)

        base_url = '/'.join(url.split('/')[:3])
        driver.set_page_load_timeout(15)
        driver.get(base_url)
        time.sleep(1)
        driver.add_cookie({'name': 'alert-rgpd', 'value': 'true',
                           'domain': base_url.replace('https://', '').replace('http://', '')})

        driver.get(url)

        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By

        email_el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "input[type='email'], input[placeholder='Email'], input[name='Email']"))
        )
        email_el.clear()
        email_el.send_keys(email)
        pwd_el = driver.find_element('css selector', "input[type='password']")
        pwd_el.clear()
        pwd_el.send_keys(password)
        btn = driver.find_element('css selector', "button[type='submit']")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(3)

        driver.get(url)
        time.sleep(2)
        after_url = driver.current_url.lower()
        driver.quit()

        if 'login' in after_url or 'account' in after_url or 'connect' in after_url or 'auth' in after_url:
            return jsonify({"success": False, "error": "Identifiants incorrects"})
        return jsonify({"success": True, "message": "Connexion reussie"})

    except Exception as e:
        try: driver.quit()
        except: pass
        msg = str(e).split('\n')[0]
        return jsonify({"success": False, "error": f"Erreur serveur : {msg}"}), 500


@app.route("/fetch-collaborateurs", methods=["POST"])
def route_fetch_collaborateurs():
    """Récupère la liste des collaborateurs de l'équipe."""
    data = request.get_json(force=True)
    email    = (data.get("email")    or "").strip()
    password = (data.get("password") or "").strip()
    url      = (data.get("url")      or "").strip()

    if not email or not password or not url:
        return jsonify({"success": False, "error": "Email, mot de passe et URL requis"}), 400

    success, result = fetch_collaborateurs(email, password, url)
    if success:
        return jsonify({"success": True, "collaborateurs": result})
    else:
        return jsonify({"success": False, "error": result}), 500


@app.route("/fetch-mois", methods=["POST"])
def route_fetch_mois():
    """Récupère les données du mois pour un collaborateur."""
    data = request.get_json(force=True)
    email      = (data.get("email")      or "").strip()
    password   = (data.get("password")   or "").strip()
    url        = (data.get("url")        or "").strip()
    salarie_id = data.get("salarieId")
    mois       = data.get("mois", datetime.date.today().month)
    annee      = data.get("annee", datetime.date.today().year)

    if not email or not password or not url:
        return jsonify({"success": False, "error": "Email, mot de passe et URL requis"}), 400
    if not salarie_id:
        return jsonify({"success": False, "error": "ID salarié requis"}), 400

    success, result = fetch_mois_collaborateur(email, password, url, salarie_id, int(mois), int(annee))
    if success:
        if result.get('_debugTaches'):
            print(f"  [taches-debug] {json.dumps(result['_debugTaches'], default=str)}", flush=True)
        return jsonify({"success": True, "days": result.get('days', {}), "nomSalarie": result.get('nomSalarie', '')})
    else:
        return jsonify({"success": False, "error": result}), 500


@app.route("/fetch-all-mois", methods=["POST"])
def route_fetch_all_mois():
    """Charge le mois de tous les collaborateurs en une seule session Chrome."""
    data = request.get_json(force=True)
    email    = (data.get("email")    or "").strip()
    password = (data.get("password") or "").strip()
    url      = (data.get("url")      or "").strip()
    ids      = data.get("salarieIds", [])
    mois     = int(data.get("mois", datetime.date.today().month))
    annee    = int(data.get("annee", datetime.date.today().year))

    if not email or not password or not url:
        return jsonify({"success": False, "error": "Email, mot de passe et URL requis"}), 400
    if not ids:
        return jsonify({"success": False, "error": "Aucun ID collaborateur"}), 400

    success, result = fetch_all_mois(email, password, url, ids, mois, annee)
    if success:
        return jsonify({"success": True, "results": result})
    else:
        return jsonify({"success": False, "error": result}), 500


@app.route("/valider", methods=["POST"])
def route_valider():
    """Valide les jours sélectionnés pour un collaborateur."""
    data = request.get_json(force=True)
    email      = (data.get("email")      or "").strip()
    password   = (data.get("password")   or "").strip()
    url        = (data.get("url")        or "").strip()
    salarie_id = data.get("salarieId")
    dates      = data.get("dates", [])
    mois       = data.get("mois", datetime.date.today().month)
    annee      = data.get("annee", datetime.date.today().year)

    if not email or not password or not url:
        return jsonify({"success": False, "error": "Email, mot de passe et URL requis"}), 400
    if not salarie_id:
        return jsonify({"success": False, "error": "ID salarié requis"}), 400
    if not dates:
        return jsonify({"success": False, "error": "Aucune date à valider"}), 400

    success, message = valider_jours_selenium(email, password, url, salarie_id, dates, int(mois), int(annee))
    if success:
        return jsonify({"success": True, "message": message})
    else:
        return jsonify({"success": False, "error": message}), 500


@app.route("/debug-vue", methods=["POST"])
def route_debug_vue():
    """Debug: dump la structure Vue brute d'un collaborateur."""
    data = request.get_json(force=True)
    email      = (data.get("email")      or "").strip()
    password   = (data.get("password")   or "").strip()
    url        = (data.get("url")        or "").strip()
    salarie_id = data.get("salarieId")
    mois       = data.get("mois", datetime.date.today().month)
    annee      = data.get("annee", datetime.date.today().year)

    driver = None
    try:
        driver = _make_driver()
        url_collab = _re.sub(r'idContrat=\d+', f'idContrat={salarie_id}', url)
        _navigate_ecol(driver, email, password, url_collab, int(mois), int(annee))

        result = driver.execute_script("""
            // Trouver Vue
            var candidates = ['#variable-paie', '#vueSaisieRapide', '#app'];
            var vueEl = null;
            for (var c = 0; c < candidates.length; c++) {
                try {
                    var el = document.querySelector(candidates[c]);
                    if (el && el.__vue__) { vueEl = el; break; }
                } catch(e) {}
            }
            if (!vueEl) {
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {
                    try {
                        if (all[i].__vue__ && all[i].__vue__.$data && Object.keys(all[i].__vue__.$data).length > 3) {
                            vueEl = all[i]; break;
                        }
                    } catch(e) {}
                }
            }
            if (!vueEl || !vueEl.__vue__) return {error: 'NO_VUE'};
            var vm = vueEl.__vue__;
            var data = vm.$data;

            // Dump les clés du modèle
            var info = {
                vueId: vueEl.id || vueEl.tagName,
                dataKeys: Object.keys(data),
                url: window.location.href
            };

            // Si model existe, dumper sa structure
            if (data.model) {
                info.modelKeys = Object.keys(data.model);
                if (data.model.Modele) {
                    var modele = data.model.Modele;
                    var keys = Object.keys(modele);
                    info.modeleCount = keys.length;
                    // Dumper le premier jour en détail
                    if (keys.length > 0) {
                        var first = modele[keys[0]];
                        info.sampleDay = JSON.parse(JSON.stringify(first));
                    }
                    // Dumper un jour travaillé (chercher mars)
                    for (var k in modele) {
                        var j = modele[k];
                        if (j && j.Mois === parseInt(new URLSearchParams(window.location.search).get('mois'))
                            && j.Horaires && j.Horaires.length > 0) {
                            info.sampleWorkDay = JSON.parse(JSON.stringify(j));
                            break;
                        }
                    }
                }
                info.modelNomSalarie = data.model.NomSalarie;
                info.modelAllDone = data.model.AllDone;
            }

            return info;
        """)

        driver.quit()
        return jsonify({"success": True, "debug": result})

    except Exception as e:
        if driver:
            try: driver.quit()
            except: pass
        return jsonify({"success": False, "error": str(e)}), 500


# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*50}")
    print(f"  Serveur Validation Manager — port {port}")
    print(f"  Test : http://localhost:{port}/ping")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
