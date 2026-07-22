/**
 * WebApp para buscar trabalhos fotográficos por data ou período.
 *
 * Suporta:
 *   ?dia=hoje
 *   ?dia=ontem
 *   ?data=YYYY-MM-DD
 *   ?inicio=YYYY-MM-DD&fim=YYYY-MM-DD
 *
 * Resposta:
 * {
 *   ids: ["22896", "22897"],
 *   trabalhos: [
 *     {
 *       id: "22896",
 *       cliente: "Nome do cliente",
 *       endereco: "Rua das Flores, 120",
 *       rua: "Rua das Flores, 120",
 *       fotografo: "Thiago",
 *       editorFoto: "Letícia",
 *       servico: "Fotografia",
 *       status: "Confirmado",
 *       dataHora: "20/07/2026 14:00",
 *       horario: "14h00",
 *       nomeColecao: "22896 - Rua das Flores, 120"
 *     }
 *   ]
 * }
 */

const SPREADSHEET_ID = '1W0GBdACk4B1y_tUquBjcpZPbq-WJL_iFjgWDyTezP4M';
const SHEET_NAME = 'fotografias';
const TZ = 'America/Sao_Paulo';

// Índices 1-based
// A = ID
// B = Status
// D = Cliente
// F = Rua
// I = Editor Foto
// K = Serviço
// P = Fotógrafo
// Q = Data/hora
const COL = {
  ID: 1,
  STATUS: 2,
  CLIENTE: 4,
  RUA: 6,
  EDITOR_FOTO: 9,
  SERVICO: 11,
  FOTOGRAFO: 16,
  DATAHORA: 17
};

// Serviços válidos
const HOME_PICZ_SERVICOS = new Set([
  'fotografia',
  'fotografia + vídeo reels',
  'combo all inclusive',
  'combo all inclusive 2',
  'vídeo reels',
  'vídeo reels personalizado',
  'fotografia drone',
  'vídeo drone',
  'combo drone'
].map(normalizeText));

/* ============================================================
 * WEBAPP
 * ============================================================ */
function doGet(e) {
  try {
    const p = e && e.parameter ? e.parameter : {};

    const dia = cleanString(p.dia).toLowerCase();
    const dataUnic = cleanString(p.data);
    const inicio = cleanString(p.inicio);
    const fim = cleanString(p.fim);

    /*
     * 1) INTERVALO
     */
    if (inicio && fim) {
      const di = parseIsoDate(inicio);
      const df = parseIsoDate(fim);

      if (!di || !df) {
        return jsonResponse({
          error: 'datas inválidas'
        }, 400);
      }

      if (di.getTime() > df.getTime()) {
        return jsonResponse({
          error: 'a data inicial não pode ser posterior à data final'
        }, 400);
      }

      const trabalhos = consultaPorIntervalo(di, df);
      return jsonResponse(createPayload(trabalhos));
    }

    /*
     * 2) DATA ÚNICA
     */
    if (dataUnic) {
      const data = parseIsoDate(dataUnic);

      if (!data) {
        return jsonResponse({
          error: 'data inválida'
        }, 400);
      }

      const prefix = formatDatePrefix(data);
      const trabalhos = consultaPorData(prefix);

      return jsonResponse(createPayload(trabalhos));
    }

    /*
     * 3) HOJE / ONTEM
     */
    const alvo = normalizaDia(dia);

    if (!alvo) {
      return jsonResponse({
        error: 'Use ?dia=hoje | ontem, ou ?data=YYYY-MM-DD, ou ?inicio=YYYY-MM-DD&fim=YYYY-MM-DD'
      }, 400);
    }

    // Cache somente para hoje/ontem.
    const cacheKey = makeCacheKey(alvo);
    const cache = CacheService.getScriptCache();
    const cached = cache.get(cacheKey);

    if (cached) {
      return jsonResponse(JSON.parse(cached));
    }

    const target = getTargetDate(alvo);
    const prefix = formatDatePrefix(target);
    const trabalhos = consultaPorData(prefix);
    const payload = createPayload(trabalhos);

    cache.put(cacheKey, JSON.stringify(payload), 300);

    return jsonResponse(payload);

  } catch (err) {
    console.error(err);

    return jsonResponse({
      error: String(err),
      stack: err && err.stack ? String(err.stack) : ''
    }, 500);
  }
}

/* ============================================================
 * CRIAÇÃO DA RESPOSTA
 * ============================================================ */
function createPayload(trabalhos) {
  const ids = trabalhos.map(item => item.id);

  return {
    ids,
    trabalhos,
    total: trabalhos.length,
    geradoEm: Utilities.formatDate(
      new Date(),
      TZ,
      "yyyy-MM-dd'T'HH:mm:ssXXX"
    )
  };
}

/* ============================================================
 * FUNÇÕES DE DATA
 * ============================================================ */
function parseIsoDate(str) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(str)) {
    return null;
  }

  const partes = str.split('-');
  const yyyy = Number(partes[0]);
  const MM = Number(partes[1]);
  const dd = Number(partes[2]);

  const date = new Date(yyyy, MM - 1, dd, 0, 0, 0, 0);

  if (
    date.getFullYear() !== yyyy ||
    date.getMonth() !== MM - 1 ||
    date.getDate() !== dd
  ) {
    return null;
  }

  return date;
}

function formatDatePrefix(dateObj) {
  return Utilities.formatDate(dateObj, TZ, 'dd/MM/yyyy');
}

function normalizaDia(value) {
  if (value === 'hoje' || value === 'today') {
    return 'hoje';
  }

  if (value === 'ontem' || value === 'yesterday') {
    return 'ontem';
  }

  return null;
}

function getTargetDate(tipo) {
  const now = new Date();

  const yyyy = Number(Utilities.formatDate(now, TZ, 'yyyy'));
  const MM = Number(Utilities.formatDate(now, TZ, 'MM'));
  const dd = Number(Utilities.formatDate(now, TZ, 'dd'));

  const base = new Date(yyyy, MM - 1, dd, 0, 0, 0, 0);

  if (tipo === 'ontem') {
    base.setDate(base.getDate() - 1);
  }

  return base;
}

/* ============================================================
 * CONSULTA POR INTERVALO
 * ============================================================ */
function consultaPorIntervalo(dataInicial, dataFinal) {
  const sheetData = loadSheetBlock();

  const inicio = new Date(dataInicial);
  inicio.setHours(0, 0, 0, 0);

  const fimExclusivo = new Date(dataFinal);
  fimExclusivo.setHours(0, 0, 0, 0);
  fimExclusivo.setDate(fimExclusivo.getDate() + 1);

  const trabalhos = [];

  for (const row of sheetData.values) {
    const item = parseWorkRow(row, sheetData);

    if (!item) {
      continue;
    }

    const data = parseBrDateTime(item.dataHora);

    if (!data) {
      continue;
    }

    if (data >= inicio && data < fimExclusivo) {
      trabalhos.push(item);
    }
  }

  return sortAndDeduplicate(trabalhos);
}

/* ============================================================
 * CONSULTA RÁPIDA POR DATA
 * ============================================================ */
function consultaPorData(prefix) {
  const sheetData = loadSheetBlock();
  const trabalhos = [];

  for (const row of sheetData.values) {
    const item = parseWorkRow(row, sheetData);

    if (!item) {
      continue;
    }

    if (item.dataHora.startsWith(prefix)) {
      trabalhos.push(item);
    }
  }

  return sortAndDeduplicate(trabalhos);
}

/* ============================================================
 * CONVERSÃO DA LINHA
 * ============================================================ */
function parseWorkRow(row, indexes) {
  const status = cleanString(row[indexes.statusIdx]);
  const normalizedStatus = normalizeText(status);

  if (normalizedStatus === 'cancelado') {
    return null;
  }

  const servico = cleanString(row[indexes.serviceIdx]);

  if (!HOME_PICZ_SERVICOS.has(normalizeText(servico))) {
    return null;
  }

  const id = cleanString(row[indexes.idIdx]);

  if (!id) {
    return null;
  }

  const dataHora = cleanString(row[indexes.dateTimeIdx]);

  if (!dataHora) {
    return null;
  }

  const cliente =
    cleanString(row[indexes.clientIdx]) ||
    'Cliente não informado';

  const endereco =
    cleanString(row[indexes.streetIdx]) ||
    'Rua não informada';

  const fotografo =
    cleanString(row[indexes.photographerIdx]) ||
    'Fotógrafo não informado';

  const editorFoto =
    cleanString(row[indexes.editorFotoIdx]) ||
    'Editor de foto não informado';

  const horario = formatTimeFromDateTime(dataHora);

  return {
    id,
    cliente,
    endereco,
    rua: endereco,
    fotografo,
    editorFoto,
    servico,
    status,
    dataHora,
    horario,
    nomeColecao: `${id} - ${endereco}`
  };
}

/* ============================================================
 * ORDENAÇÃO E REMOÇÃO DE DUPLICADOS
 * ============================================================ */
function sortAndDeduplicate(trabalhos) {
  const byId = new Map();

  for (const trabalho of trabalhos) {
    /*
     * Caso o mesmo ID apareça mais de uma vez, mantém a última
     * ocorrência encontrada na planilha.
     */
    byId.set(trabalho.id, trabalho);
  }

  return Array.from(byId.values()).sort((a, b) => {
    const dateA = parseBrDateTime(a.dataHora);
    const dateB = parseBrDateTime(b.dataHora);

    const timestampA = dateA ? dateA.getTime() : 0;
    const timestampB = dateB ? dateB.getTime() : 0;

    if (timestampA !== timestampB) {
      return timestampA - timestampB;
    }

    return a.id.localeCompare(b.id, 'pt-BR', {
      numeric: true,
      sensitivity: 'base'
    });
  });
}

/* ============================================================
 * CARREGAMENTO DA PLANILHA
 * ============================================================ */
function loadSheetBlock() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(SHEET_NAME);

  if (!sheet) {
    throw new Error(`Aba não encontrada: ${SHEET_NAME}`);
  }

  const lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    return {
      values: [],
      idIdx: COL.ID - 1,
      statusIdx: COL.STATUS - 1,
      clientIdx: COL.CLIENTE - 1,
      streetIdx: COL.RUA - 1,
      editorFotoIdx: COL.EDITOR_FOTO - 1,
      serviceIdx: COL.SERVICO - 1,
      photographerIdx: COL.FOTOGRAFO - 1,
      dateTimeIdx: COL.DATAHORA - 1
    };
  }

  /*
   * A última coluna necessária continua sendo Q, portanto não
   * aumenta o tamanho do bloco atualmente lido.
   */
  const range = sheet.getRange(
    2,
    1,
    lastRow - 1,
    COL.DATAHORA
  );

  return {
    values: range.getDisplayValues(),
    idIdx: COL.ID - 1,
    statusIdx: COL.STATUS - 1,
    clientIdx: COL.CLIENTE - 1,
    streetIdx: COL.RUA - 1,
    editorFotoIdx: COL.EDITOR_FOTO - 1,
    serviceIdx: COL.SERVICO - 1,
    photographerIdx: COL.FOTOGRAFO - 1,
    dateTimeIdx: COL.DATAHORA - 1
  };
}

/* ============================================================
 * PARSE DE DATA BRASILEIRA
 *
 * Aceita, por exemplo:
 *   20/07/2026
 *   20/07/2026 14:30
 *   20/07/2026 14:30:25
 * ============================================================ */
function parseBrDateTime(str) {
  const match = cleanString(str).match(
    /^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/
  );

  if (!match) {
    return null;
  }

  const dd = Number(match[1]);
  const MM = Number(match[2]);
  const yyyy = Number(match[3]);
  const hour = Number(match[4] || 0);
  const minute = Number(match[5] || 0);
  const second = Number(match[6] || 0);

  const date = new Date(
    yyyy,
    MM - 1,
    dd,
    hour,
    minute,
    second,
    0
  );

  if (
    date.getFullYear() !== yyyy ||
    date.getMonth() !== MM - 1 ||
    date.getDate() !== dd
  ) {
    return null;
  }

  return date;
}

/* ============================================================
 * FORMATAÇÃO DE HORÁRIO
 * ============================================================ */
function formatTimeFromDateTime(value) {
  const match = cleanString(value).match(
    /(?:^|\s)(\d{1,2}):(\d{2})(?::\d{2})?(?:\s|$)/
  );

  if (!match) {
    return '';
  }

  const hour = String(Number(match[1])).padStart(2, '0');
  return `${hour}h${match[2]}`;
}

/* ============================================================
 * TEXTO
 * ============================================================ */
function cleanString(value) {
  return value === null || value === undefined
    ? ''
    : String(value).trim();
}

function normalizeText(value) {
  return cleanString(value)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

/* ============================================================
 * CACHE
 * ============================================================ */
function makeCacheKey(alvo) {
  const stamp = Utilities.formatDate(
    new Date(),
    TZ,
    'yyyyMMdd'
  );

  /*
   * v4 impede que o cache antigo, que não continha Rua, horário
   * e nomeColecao, seja devolvido depois da atualização.
   */
  return `fotografias:v4:${alvo}:${stamp}`;
}

/* ============================================================
 * JSON RESPONSE
 * ============================================================ */
function jsonResponse(obj, status) {
  const output = ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);

  /*
   * Apps Script ContentService normalmente não oferece controle
   * real do status HTTP. Mantido por compatibilidade.
   */
  if (status && typeof output.setResponseCode === 'function') {
    try {
      output.setResponseCode(status);
    } catch (_) {
      // O ambiente atual pode não oferecer setResponseCode.
    }
  }

  return output;
}
