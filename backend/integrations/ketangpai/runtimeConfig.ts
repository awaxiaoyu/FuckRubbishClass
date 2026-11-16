// Desktop mode reads the shared JSON preferences; standalone CLI keeps config.ts.
import fs from "fs";
import * as defaults from "./config.example";

type RuntimeConfig = {
  [K in keyof typeof defaults]: typeof defaults[K] extends boolean ? boolean
    : typeof defaults[K] extends number ? number
    : typeof defaults[K] extends string ? string : typeof defaults[K];
};

function loadConfig(): RuntimeConfig {
  const path = process.env.COURSE_HUB_KTP_CONFIG;
  if (!path) {
    // Dynamic require preserves the upstream CLI without requiring its local
    // credentials file during TypeScript compilation.
    return require("./config") as RuntimeConfig;
  }
  let profile: RuntimeConfig;
  try {
    profile = JSON.parse(fs.readFileSync(path, "utf8").replace(/^\uFEFF/, "")).ketangpai;
  } catch {
    throw new Error("无法读取桌面配置，请在应用设置中重新保存课堂派配置。");
  }
  if (!profile || profile.CONFIG_VERSION !== 2 || !Array.isArray(profile.USERS)
      || !profile.USERS.length || !Array.isArray(profile.CLASSES) || !profile.CLASSES.length) {
    throw new Error("课堂派配置不完整，请在应用设置中添加账号和课程并保存。");
  }
  return profile;
}

const config = loadConfig();
export const CONFIG_VERSION = config.CONFIG_VERSION;
export const WEBSOCKET_SERVER_PORT = config.WEBSOCKET_SERVER_PORT;
export const WEBSOCKET_ENABLE_TLS = config.WEBSOCKET_ENABLE_TLS;
export const TLS_CERT_PATH = config.TLS_CERT_PATH;
export const TLS_KEY_PATH = config.TLS_KEY_PATH;
export const WEBSOCKET_SERVER_PATH = config.WEBSOCKET_SERVER_PATH;
export const ENABLE_TELEGRAM_BOT = config.ENABLE_TELEGRAM_BOT;
export const TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN;
export const TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID;
export const TELEGRAM_BOT_PRIVACY_MODE = config.TELEGRAM_BOT_PRIVACY_MODE;
export const TELEGRAM_BOT_PRIVACY_MODE_WHITELIST = config.TELEGRAM_BOT_PRIVACY_MODE_WHITELIST;
export const TELEGRAM_BOT_REPLY_INVALID_QRCODE = config.TELEGRAM_BOT_REPLY_INVALID_QRCODE;
export const TELEGRAM_BOT_API_URL = config.TELEGRAM_BOT_API_URL;
export const USERS = config.USERS;
export const CLASSES = config.CLASSES;
export const DEFAULT_LATITUDE = config.DEFAULT_LATITUDE;
export const DEFAULT_LONGITUDE = config.DEFAULT_LONGITUDE;
export const ENABLE_IGNORE_QRCODE_EXPIRE = config.ENABLE_IGNORE_QRCODE_EXPIRE;
export const 签到_CHECK_INTERVAL_SECONDS = config.签到_CHECK_INTERVAL_SECONDS;
export const MIN_DELAY_SECONDS = config.MIN_DELAY_SECONDS;
export const MAX_DELAY_SECONDS = config.MAX_DELAY_SECONDS;
export const FAKE_IP_PREFIX = config.FAKE_IP_PREFIX;
export const ENABLE_互动答题_CHECK = config.ENABLE_互动答题_CHECK;
export const 互动答题_CHECK_INTERVAL_SECONDS = config.互动答题_CHECK_INTERVAL_SECONDS;
export const 互动答题_CHECK_DELAY_SECONDS = config.互动答题_CHECK_DELAY_SECONDS;
