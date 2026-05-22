# -*- coding: utf-8 -*-
"""Cliente para API Planet Basemaps.

Baseado no SDK oficial planet-client-python usado pelo Planet Explorer.
Adaptado do plugin Floresta+ Amazônia.
"""

import os
import json
import base64
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class PlanetClient:
    """Cliente para acesso à API Planet Basemaps."""

    BASE_URL = "https://api.planet.com/"
    BASEMAPS_URL = "https://api.planet.com/basemaps/v1"
    TILES_URL = "https://tiles.planet.com/basemaps/v1"

    def __init__(self):
        self.api_key = None
        self.user_email = None
        self.session = None
        self._logged_in = False
        self._user_data = None

    @property
    def is_logged_in(self):
        return self._logged_in and self.api_key is not None

    def _create_session(self):
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'planet-client-python/1.5.2',
            'X-Planet-App': 'python-client',
        })
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        return session

    def login(self, email, password):
        try:
            session = self._create_session()
            url = f"{self.BASE_URL}v0/auth/login"
            result = session.post(url, json={'email': email, 'password': password})

            if result.status_code == 200:
                jwt = result.text
                payload = jwt.split('.')[1]
                rem = len(payload) % 4
                if rem > 0:
                    payload += '=' * (4 - rem)
                payload = base64.urlsafe_b64decode(payload.encode('utf-8'))
                user_data = json.loads(payload.decode('utf-8'))
                self.api_key = user_data.get('api_key')
                self.user_email = email
                self._user_data = user_data
                self.session = session
                self._logged_in = True
                return True, "Login realizado com sucesso!"
            elif result.status_code == 401:
                try:
                    msg = json.loads(result.text).get('message', 'Credenciais inválidas')
                except Exception:
                    msg = result.text or 'Credenciais inválidas'
                return False, f"Credenciais inválidas: {msg}"
            elif result.status_code == 403:
                return self._try_basic_auth_login(email, password)
            else:
                return False, f"Erro {result.status_code}: {result.text}"
        except requests.exceptions.Timeout:
            return False, "Timeout — servidor não respondeu"
        except requests.exceptions.ConnectionError:
            return False, "Erro de conexão — verifique sua internet"
        except Exception as e:
            return False, f"Erro: {e}"

    def _try_basic_auth_login(self, email, password):
        try:
            session = self._create_session()
            session.auth = (email, password)
            url = f"{self.BASEMAPS_URL}/mosaics"
            response = session.get(url, params={"_page_size": 1}, timeout=20)
            if response.status_code == 200:
                self.api_key = email
                self.user_email = email
                self.session = session
                self._logged_in = True
                return True, "Login realizado com sucesso (Basic Auth)!"
            elif response.status_code == 401:
                return False, "Credenciais inválidas"
            else:
                return False, f"Erro {response.status_code}: Acesso negado"
        except Exception as e:
            return False, f"Erro no login alternativo: {e}"

    def login_with_api_key(self, api_key):
        try:
            session = self._create_session()
            session.auth = (api_key.strip(), '')
            url = f"{self.BASEMAPS_URL}/mosaics"
            response = session.get(url, params={"_page_size": 1}, timeout=15)
            if response.status_code == 200:
                self.api_key = api_key.strip()
                self.session = session
                self._logged_in = True
                return True, "API Key válida!"
            elif response.status_code == 401:
                return False, "API Key inválida"
            else:
                return False, f"Erro: {response.status_code}"
        except Exception as e:
            return False, f"Erro: {e}"

    def logout(self):
        self.api_key = None
        self.user_email = None
        self._logged_in = False
        self._user_data = None
        if self.session:
            self.session.close()
            self.session = None

    def _get_auth(self):
        if self.api_key:
            return (self.api_key, '')
        return None

    def _request(self, url, params=None):
        if not self.is_logged_in or not self.session:
            return None
        try:
            if not self.session.auth:
                self.session.auth = self._get_auth()
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    def list_mosaics(self, name_contains=None, limit=200):
        if not self.is_logged_in:
            return []
        try:
            url = f"{self.BASEMAPS_URL}/mosaics"
            params = {"v": "1.5", "_page_size": 100}
            if name_contains:
                params["name__contains"] = name_contains
            data = self._request(url, params)
            if not data:
                return []
            all_mosaics = data.get("mosaics", [])
            while "_next" in data.get("_links", {}) and len(all_mosaics) < limit:
                next_url = data["_links"]["_next"]
                response = self.session.get(next_url, timeout=30)
                if response.status_code != 200:
                    break
                data = response.json()
                all_mosaics.extend(data.get("mosaics", []))
            all_mosaics.sort(key=lambda x: x.get("last_acquired", ""), reverse=True)
            return all_mosaics
        except Exception:
            return []

    def get_tile_url(self, mosaic_name):
        if not self.is_logged_in or not self.api_key:
            return None
        return (
            f"{self.TILES_URL}/planet-tiles/{mosaic_name}"
            f"/gmap/{{z}}/{{x}}/{{y}}.png?api_key={self.api_key}"
        )

    def get_mosaic_display_name(self, mosaic):
        try:
            first_acquired = mosaic.get("first_acquired", "")
            if first_acquired:
                from datetime import datetime
                dt = datetime.fromisoformat(first_acquired.replace("Z", "+00:00"))
                meses = [
                    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
                ]
                return f"{meses[dt.month - 1]} {dt.year}"
        except Exception:
            pass
        return mosaic.get("name", "Mosaico")


planet_client = PlanetClient()
