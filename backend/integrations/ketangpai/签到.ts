import { 签到Events } from "./events";
import { getAuthenticatedHeaders, getReqtimestamp, getCurrentClass, randomRange, fancyFetch, getDeduplicatedClasses, fakeIp } from "./util";
import { credentials } from "./auth";
import { DEFAULT_LATITUDE, DEFAULT_LONGITUDE, ENABLE_IGNORE_QRCODE_EXPIRE, MAX_DELAY_SECONDS, MIN_DELAY_SECONDS, 签到_CHECK_INTERVAL_SECONDS } from "./runtimeConfig";
import { ClassType, CredentialType } from "./types";
import { LabelledLogger } from "./logger";
import { configuredLocation, getNonQrcodeUnusual, isAttendanceSucceeded } from "./attendanceSemantics";

const logger = new LabelledLogger("签到");

let hasPending签到 = false;
let 签到ProgressCount = 0;
// 最近一次发现的二维码考勤 id；自动 checkin / 扫码兜底共用
let pendingQrcode签到Id: string | null = null;
// 防止 delay 等待期间扫码兜底与自动 checkin 重复提交
let qrcodeCheckinStarted = false;

export function register签到EventHandlers() {
  签到Events.on("new数字签到", (class_, delaySeconds, 签到Id) => delayWrapper(delaySeconds, process数字签到, class_, 签到Id));
  签到Events.on("newGps签到", (class_, delaySeconds, 签到Id) => delayWrapper(delaySeconds, processGps签到, class_, 签到Id));
  签到Events.on("new签入签出签到", (class_, delaySeconds, 签到Id) => delayWrapper(delaySeconds, process签入签出签到, class_, 签到Id));
  // 开启「无视有效期」时走 AttenceApi/checkin；关闭时仅等待扫码提交
  签到Events.on("newQrcode签到", (class_, delaySeconds, 签到Id) => {
    if (ENABLE_IGNORE_QRCODE_EXPIRE) {
      delayWrapper(delaySeconds, process二维码签到, class_, 签到Id);
      return;
    }
    签到Events.once("cancel", () => {
      签到Events.emit("cancelSuccess");
      hasPending签到 = false;
      pendingQrcode签到Id = null;
      qrcodeCheckinStarted = false;
    });
  });

  // 扫码提交：开启无视有效期且已有 pending id 时走 checkin，否则走 AttenceResult
  签到Events.on("submitQrcode", (ticketid, expire, sign) => processQrcode签到(ticketid, expire, sign));

  签到Events.on("manualCheck", () => {
    for (const class_ of getDeduplicatedClasses()) checkIncomplete签到(class_);
  });
}

export function register签到Watchers() {
  setInterval(签到WatcherLoop, 签到_CHECK_INTERVAL_SECONDS * 1000);
}

function 签到WatcherLoop() {
  const currentClass = getCurrentClass();

  if (currentClass) {
    checkIncomplete签到(currentClass);
  }
}

async function delayWrapper(delaySeconds: number, fn: Function, ...args: unknown[]) {
  logger.info(`Delaying 签到 for ${delaySeconds} seconds...`);

  签到Events.once("cancel", () => {
    签到Events.emit("cancelSuccess");
    签到Events.removeAllListeners("cancel");
    签到Events.removeAllListeners("skip");
    hasPending签到 = false;
    pendingQrcode签到Id = null;
    qrcodeCheckinStarted = false;
    fn = () => {};
  });

  await new Promise<void>((resolve) => {
    setTimeout(resolve, delaySeconds * 1000);
    签到Events.once("skip", () => {
      签到Events.emit("skipSuccess");
      resolve();
    });
  });

  签到Events.removeAllListeners("cancel");
  签到Events.removeAllListeners("skip");

  fn(...args);
}

async function checkIncomplete签到(class_: ClassType) {
  const headers = getAuthenticatedHeaders(credentials[0]);

  const _response = await fancyFetch("https://openapiv5.ketangpai.com/AttenceApi/getNotFinishAttenceStudent", {
    method: "POST",
    headers,
    body: {
      courseid: class_.classId,
      reqtimestamp: getReqtimestamp(),
    },
  });
  const response: any = await _response.json();

  const incomplete签到s = response.data.lists;
  const 签到 = incomplete签到s[0];

  // 在 ktpwarp-server 动作之前就已撤销或结束的签到，按取消签到处理
  if (!签到 && hasPending签到) {
    logger.warn("签到 has been cancelled before we move on!");
    hasPending签到 = false;
    pendingQrcode签到Id = null;
    qrcodeCheckinStarted = false;
    签到Events.emit("cancel");
  }
  if (!签到 || hasPending签到) return;

  hasPending签到 = incomplete签到s.length > 0;

  const delaySeconds = randomRange(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS);

  if (签到.type == "1") {
    logger.info(`Found 数字签到 for class ${class_.friendlyName} (${class_.classId})`);
    签到Events.emit("new数字签到", class_, delaySeconds, 签到.id);
  }

  if (签到.type == "2") {
    logger.info(`Found GPS 签到 for class ${class_.friendlyName} (${class_.classId})`);
    签到Events.emit("newGps签到", class_, delaySeconds, 签到.id);
  }

  if (签到.type == "3") {
    logger.info(`Found 二维码签到 for class ${class_.friendlyName} (${class_.classId})`);
    pendingQrcode签到Id = String(签到.id);
    qrcodeCheckinStarted = false;
    签到Events.emit("newQrcode签到", class_, delaySeconds, 签到.id);
  }

  if (签到.type == "4") {
    logger.info(`Found 签入签出签到 for class ${class_.friendlyName} (${class_.classId})`);
    签到Events.emit("new签入签出签到", class_, delaySeconds, 签到.id);
  }
}

async function process数字签到(class_: ClassType, 签到Id: string) {
  logger.info(`Processing 数字签到 for class ${class_.friendlyName} (${class_.classId})...`);

  // extract 数字签到 code
  try {
    const headers = getAuthenticatedHeaders(credentials[0]);

    const _response = await fancyFetch("https://openapiv5.ketangpai.com/AttenceApi/getDigitAttence", {
      method: "POST",
      headers,
      body: {
        id: 签到Id,
        reqtimestamp: getReqtimestamp(),
      },
    });
    const response: any = await _response.json();

    const code = response.data?.data?.code;
    if (response.status !== 1 || (typeof code !== "string" && typeof code !== "number") || !String(code).trim()) {
      throw new Error(response.message || "数字签到响应缺少有效签到码");
    }
    logger.info(`数字签到 code is ${code}`);
    签到Events.emit("数字签到code", String(code));

    await Promise.all(
      credentials.map((credential) => executeNonQrcode签到(credential, 签到Id, "", "", String(code)))
    );
  } catch (e) {
    logger.error(`Error fetching 数字签到 code: ${e}`);
    credentials.map((credential) => {
      签到Events.emit("签到failure", credential, "无法获取数字签到码，签到已结束或被删除？");
    });
    hasPending签到 = false;
  }
}

async function processGps签到(class_: ClassType, 签到Id: string) {
  logger.info(`Processing GPS 签到 for class ${class_.friendlyName} (${class_.classId})...`);

  const location = configuredLocation(class_, DEFAULT_LATITUDE, DEFAULT_LONGITUDE);
  if (!location) { reportMissingLocation("GPS 签到"); return; }
  const { latitude, longitude } = location;

  await Promise.all(
    credentials.map((credential) => executeNonQrcode签到(credential, 签到Id, latitude, longitude, "", getNonQrcodeUnusual("gps")))
  );
}

async function process签入签出签到(class_: ClassType, 签到Id: string) {
  logger.info(`Processing 签入签出签到 for class ${class_.friendlyName} (${class_.classId})...`);

  const location = configuredLocation(class_, DEFAULT_LATITUDE, DEFAULT_LONGITUDE);
  if (!location) { reportMissingLocation("签入签出签到"); return; }
  const { latitude, longitude } = location;

  await Promise.all(
    credentials.map((credential) => executeNonQrcode签到(credential, 签到Id, latitude, longitude, "", getNonQrcodeUnusual("checkinCheckout")))
  );
}

async function process二维码签到(class_: ClassType, 签到Id: string) {
  if (qrcodeCheckinStarted) {
    logger.info(`Skip duplicate 二维码自动签到 for ${签到Id}`);
    return;
  }
  qrcodeCheckinStarted = true;
  logger.info(`Processing 二维码签到 via AttenceApi/checkin for class ${class_.friendlyName} (${class_.classId})...`);
  pendingQrcode签到Id = 签到Id;
  // type=3 走 checkin：body 不含 ticketid/expire/sign，服务端只校验考勤窗口
  await Promise.all(credentials.map((credential) => executeNonQrcode签到(credential, 签到Id, "", "", "", 0)));
}

function reportMissingLocation(attendanceType: string) {
  const message = `${attendanceType}需要在 设置 → 课堂派 → 课程时间表 中填写有效的实际位置坐标；未提交签到。`;
  logger.warn(message);
  for (const credential of credentials) 签到Events.emit("签到failure", credential, message);
  hasPending签到 = false;
}

// 阻止多个二维码签到同时进行，在极低概率下 ktpwarp-web 可能会在一次扫码后提交数次相同的二维码
let qrcode签到Lock = false;

async function processQrcode签到(ticketid: string, expire: string, sign: string) {
  logger.info(`Processing qrcode 签到 for ticketid ${ticketid}...`);

  if (qrcode签到Lock) return;
  qrcode签到Lock = true;

  try {
    if (ENABLE_IGNORE_QRCODE_EXPIRE && pendingQrcode签到Id) {
      if (qrcodeCheckinStarted) {
        logger.info(`Skip duplicate qrcode checkin fallback for ${pendingQrcode签到Id}`);
        return;
      }
      qrcodeCheckinStarted = true;
      // 已发现在途二维码考勤：直接 checkin，无视二维码有效期；并跳过 delay 等待
      logger.info(`Using AttenceApi/checkin fallback for pending qrcode attendance ${pendingQrcode签到Id}`);
      签到Events.emit("skip");
      await Promise.all(credentials.map((credential) => executeNonQrcode签到(credential, pendingQrcode签到Id!, "", "", "", 0)));
      return;
    }
    // 关闭无视有效期，或监测尚未发现考勤时：走旧票据接口
    await Promise.all(credentials.map((credential) => executeQrcode签到(credential, ticketid, expire, sign)));
  } finally {
    qrcode签到Lock = false;
  }
}

async function executeNonQrcode签到(
  credential: CredentialType,
  签到Id: string,
  latitude = "",
  longitude = "",
  code = "",
  unusual: 0 | 1 | 2 = 0,
) {
  logger.info(`Executing non-qrcode 签到 for user ${credential.friendlyName}...`);

  fakeIp.reset();
  try {
    const headers = getAuthenticatedHeaders(credential);

    const _response = await fancyFetch("https://openapiv5.ketangpai.com/AttenceApi/checkin", {
      method: "POST",
      headers,
      body: {
        id: 签到Id,
        code,
        unusual,
        latitude,
        longitude,
        accuracy: "",
        appid: "",
        clienttype: 1,
        reqtimestamp: getReqtimestamp(),
      },
    });
    const response: any = await _response.json();

    // The current client resolves non-QR attendance when the envelope status is 1.
    // Keep data.state in logs only: some successful response variants omit it.
    if (isAttendanceSucceeded(response)) {
      logger.info(`Successfully 签到 for user ${credential.friendlyName} (state: ${response.data?.state ?? "not provided"})`);
      签到Events.emit("签到success", credential);
    } else {
      const reason = response.message || `API status ${response.status ?? "missing"}`;
      logger.info(`Failed to 签到 for user ${credential.friendlyName}, reason: ${reason}`);
      签到Events.emit("签到failure", credential, reason);
    }
  } catch (error) {
    logger.error(`签到 request failed for user ${credential.friendlyName}: ${String(error)}`);
    签到Events.emit("签到failure", credential, "签到请求失败，请检查网络和日志后重试");
  } finally {
    签到ProgressCount++;
    if (签到ProgressCount === credentials.length) {
      logger.info(`All 签到 finished`);
      签到ProgressCount = 0;
      hasPending签到 = false;
      pendingQrcode签到Id = null;
      qrcodeCheckinStarted = false;
    }
  }
}

async function executeQrcode签到(credential: CredentialType, ticketid: string, expire: string, sign: string) {
  logger.info(`Executing qrcode 签到 for user ${credential.friendlyName}...`);

  fakeIp.reset();

  try {
    const headers = getAuthenticatedHeaders(credential);

    const _response = await fancyFetch("https://openapiv5.ketangpai.com/AttenceApi/AttenceResult", {
      method: "POST",
      headers,
      body: {
        ticketid,
        expire,
        sign,
        reqtimestamp: getReqtimestamp(),
      },
    });
    const response: any = await _response.json();

    if (isAttendanceSucceeded(response)) {
      logger.info(`Successfully 签到 for user ${credential.friendlyName} (state: ${response.data?.state ?? "not provided"})`);
      签到Events.emit("签到success", credential);
    } else {
      const reason = response.message || `API status ${response.status ?? "missing"}`;
      logger.info(`Failed to 签到 for user ${credential.friendlyName}, reason: ${reason}`);
      签到Events.emit("签到failure", credential, reason);
    }
  } catch (error) {
    logger.error(`二维码签到 request failed for user ${credential.friendlyName}: ${String(error)}`);
    签到Events.emit("签到failure", credential, "二维码签到请求失败，请检查网络和日志后重试");
  } finally {
    签到ProgressCount++;
    if (签到ProgressCount === credentials.length) {
      logger.info(`All 签到 finished`);
      签到ProgressCount = 0;
      hasPending签到 = false;
      pendingQrcode签到Id = null;
      qrcodeCheckinStarted = false;
    }
  }
}
