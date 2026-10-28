"""Standalone AI Horde smoke test. No app/database changes; standard library only."""
import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = "https://aihorde.net/api/v2"
CLIENT_AGENT = "AIPublisherSmokeTest:0.1:local-test"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class APIError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"AI Horde HTTP {status}: {message}")


def api(method, path, payload=None):
    headers = {"Accept": "application/json", "Client-Agent": CLIENT_AGENT,
               "User-Agent": "AIPublisherSmokeTest/0.1"}
    if method == "POST":
        headers["apikey"] = os.getenv("AIHORDE_API_KEY", "0000000000").strip() or "0000000000"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", errors="replace")
        raise APIError(exc.code, body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Blad polaczenia z AI Horde: {exc.reason}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Odpowiedz AI Horde przekracza limit 20 MB.")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("AI Horde nie zwrocilo poprawnego JSON.") from exc


def save_image(generation, folder, request_id):
    if generation.get("censored"):
        raise RuntimeError("Obraz zostal oznaczony jako ocenzurowany; nie zapisano go jako sukces.")
    encoded = generation.get("img")
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("Brak danych obrazu w odpowiedzi.")
    if encoded.startswith(("https://", "http://")):
        raise RuntimeError("Otrzymano URL zamiast Base64 mimo r2=false. Nie pobieram nieoczekiwanego URL.")
    if encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[-1]
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("Niepoprawne dane Base64 obrazu.") from exc
    if len(image) < 32:
        raise RuntimeError("Dane obrazu sa zbyt krotkie.")
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        extension = ".webp"
    elif image.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif image.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    else:
        raise RuntimeError("Odpowiedz nie ma sygnatury WebP, PNG ani JPEG.")
    destination = folder / (request_id + extension)
    temporary = destination.with_suffix(extension + ".part")
    temporary.write_bytes(image)
    temporary.replace(destination)
    return destination


def cancel(request_id):
    try:
        api("DELETE", f"/generate/status/{request_id}")
        print("Wyslano anulowanie niezrealizowanego zlecenia.", flush=True)
    except Exception as exc:
        print(f"Nie udalo sie potwierdzic anulowania: {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Jednorazowy test AI Horde, bez zmian aplikacji.")
    parser.add_argument("--prompt", default=(
        "Editorial illustration for an educational insurance article: a small house, "
        "a family car and a protective umbrella, clean friendly illustration, "
        "blue and teal palette, no text, no letters, no logos"))
    parser.add_argument("--output", default="test-images")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--request-id", help="Sprawdz istniejace zlecenie bez tworzenia nowego.")
    args = parser.parse_args()
    if not 30 <= args.timeout <= 1800:
        parser.error("--timeout musi byc w zakresie 30-1800 sekund")
    if not args.prompt.strip() or len(args.prompt) > 3000:
        parser.error("Prompt musi miec 1-3000 znakow")
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    request_id = None
    cancel_on_exit = False
    try:
        if args.request_id:
            request_id = str(uuid.UUID(args.request_id))
            print(f"Sprawdzam istniejace zlecenie: {request_id}", flush=True)
        else:
            payload = {
                "prompt": args.prompt.strip(),
                "params": {"width": 512, "height": 512, "steps": 20,
                           "cfg_scale": 7.0, "sampler_name": "k_euler", "n": 1},
                "nsfw": False, "censor_nsfw": True,
                "r2": False, "shared": False,
                "trusted_workers": True, "slow_workers": True,
            }
            print("Wysylam JEDNO zlecenie 512x512. Bez automatycznego ponawiania POST.", flush=True)
            accepted = api("POST", "/generate/async", payload)
            if not accepted.get("id"):
                raise RuntimeError("Brak identyfikatora zlecenia: " + json.dumps(accepted, ensure_ascii=False))
            request_id = str(uuid.UUID(accepted["id"]))
            metadata = {"request_id": request_id, "prompt": args.prompt,
                        "params": payload["params"], "provider": "aihorde"}
            (folder / (request_id + ".request.json")).write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Identyfikator: {request_id}", flush=True)
            if accepted.get("message"):
                print("Komunikat uslugi:", accepted["message"], flush=True)
        cancel_on_exit = True
        deadline = time.monotonic() + args.timeout
        errors = 0
        while time.monotonic() < deadline:
            try:
                state = api("GET", f"/generate/check/{request_id}")
                errors = 0
            except APIError as exc:
                if exc.status in {429, 500, 502, 503, 504} and errors < 4:
                    errors += 1
                    print(f"Przejsciowy HTTP {exc.status}; ponawiam tylko odczyt za 15 s.", flush=True)
                    time.sleep(15)
                    continue
                raise
            print("Status: " + json.dumps({k: state.get(k) for k in
                  ("done", "finished", "processing", "waiting", "queue_position", "wait_time", "is_possible")},
                  ensure_ascii=False), flush=True)
            if state.get("faulted"):
                raise RuntimeError("AI Horde oznaczylo zlecenie jako faulted.")
            if state.get("done"):
                result = api("GET", f"/generate/status/{request_id}")
                generations = result.get("generations") or []
                if not generations:
                    raise RuntimeError("Zlecenie zakonczone, ale bez wygenerowanego obrazu.")
                destination = save_image(generations[0], folder, request_id)
                cancel_on_exit = False
                metadata = {"request_id": request_id, "provider": "aihorde",
                            "model": generations[0].get("model"),
                            "seed": generations[0].get("seed"), "image_file": destination.name}
                (folder / (request_id + ".result.json")).write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"SUKCES. Zapisano: {destination} ({destination.stat().st_size} bajtow)", flush=True)
                print("Model:", metadata["model"], flush=True)
                return 0
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(10, remaining))
        raise RuntimeError("Przekroczono czas oczekiwania. Nie tworz automatycznie kolejnego zlecenia.")
    except KeyboardInterrupt:
        print("Przerwano przez Ctrl+C.", flush=True)
        return 130
    except Exception as exc:
        print(f"BLAD: {exc}", file=sys.stderr, flush=True)
        print("Nie potwierdzono wygenerowania i zapisu grafiki.", file=sys.stderr, flush=True)
        return 1
    finally:
        if cancel_on_exit and request_id:
            cancel(request_id)


if __name__ == "__main__":
    sys.exit(main())
