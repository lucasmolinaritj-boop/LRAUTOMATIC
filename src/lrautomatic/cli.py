from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import typer
import uvicorn

from .api import create_app
from .config import load_settings
from .models import ImportJobRequest, ImportSource

app = typer.Typer(no_args_is_help=True, help="Automação local do Lightroom Classic")


def _client(config: str) -> tuple[httpx.Client, dict[str, str]]:
    settings = load_settings(config)
    client = httpx.Client(base_url=f"http://{settings.host}:{settings.port}", timeout=60)
    return client, {"Authorization": f"Bearer {settings.api_key}"}


@app.command()
def serve(config: str = typer.Option("config.json", help="Arquivo de configuração")) -> None:
    settings = load_settings(config)
    uvicorn.run(create_app(config), host=settings.host, port=settings.port)


@app.command("import")
def import_photos(
    source: list[str] = typer.Option(None, "--source", "-s", help="Pasta ou Pasta|Nome da coleção"),
    job: Path | None = typer.Option(None, "--job", exists=True, dir_okay=False),
    collection_set: str | None = typer.Option(None),
    recursive: bool = typer.Option(False),
    smart_previews: bool = typer.Option(False, help="Registra solicitação; SDK público não executa ainda"),
    config: str = typer.Option("config.json"),
) -> None:
    if job:
        request = ImportJobRequest.model_validate_json(job.read_text(encoding="utf-8"))
    else:
        if not source:
            raise typer.BadParameter("Informe ao menos um --source ou --job")
        sources: list[ImportSource] = []
        for item in source:
            path_text, separator, collection = item.partition("|")
            sources.append(ImportSource(path=path_text, collection=collection if separator else None))
        request = ImportJobRequest(
            sources=sources,
            collection_set=collection_set,
            recursive=recursive,
            build_smart_previews=smart_previews,
        )

    client, headers = _client(config)
    with client:
        response = client.post("/api/v1/import-jobs", json=request.model_dump(mode="json"), headers=headers)
        response.raise_for_status()
        payload = response.json()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def jobs(config: str = typer.Option("config.json")) -> None:
    client, headers = _client(config)
    with client:
        response = client.get("/api/v1/import-jobs", headers=headers)
        response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command()
def status(job_id: str, config: str = typer.Option("config.json")) -> None:
    client, headers = _client(config)
    with client:
        response = client.get(f"/api/v1/import-jobs/{job_id}", headers=headers)
        response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command()
def cancel(job_id: str, config: str = typer.Option("config.json")) -> None:
    client, headers = _client(config)
    with client:
        response = client.post(f"/api/v1/import-jobs/{job_id}/cancel", headers=headers)
        response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("catalog-create")
def catalog_create(
    name: str = typer.Option(..., help="Nome do novo catálogo"),
    config: str = typer.Option("config.json"),
) -> None:
    settings = load_settings(config)
    if not settings.catalog_template or not settings.catalog_template.exists():
        raise typer.BadParameter("catalog_template ainda não foi configurado ou não existe")
    if not settings.catalog_output_root:
        raise typer.BadParameter("catalog_output_root ainda não foi configurado")
    safe_name = "".join(char for char in name if char not in '<>:"/\\|?*').strip()
    if not safe_name:
        raise typer.BadParameter("Nome de catálogo inválido")
    destination = settings.catalog_output_root / f"{safe_name}.lrcat"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise typer.BadParameter(f"Já existe: {destination}")
    shutil.copy2(settings.catalog_template, destination)
    typer.echo(str(destination))


if __name__ == "__main__":
    app()
