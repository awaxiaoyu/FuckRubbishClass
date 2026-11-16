import { ClassType } from "./types";

export type NonQrcodeUnusual = 0 | 1 | 2;

export function getNonQrcodeUnusual(type: "digit" | "gps" | "checkinCheckout"): NonQrcodeUnusual {
  return type === "gps" ? 1 : type === "checkinCheckout" ? 2 : 0;
}

export function isAttendanceSucceeded(response: { status?: unknown }): boolean {
  return response.status === 1;
}

export function configuredLocation(class_: ClassType, defaultLatitude: string, defaultLongitude: string) {
  const hasCourseLocation = Boolean(class_.latitude?.trim() || class_.longitude?.trim());
  const latitude = (hasCourseLocation ? class_.latitude || "" : defaultLatitude).trim();
  const longitude = (hasCourseLocation ? class_.longitude || "" : defaultLongitude).trim();
  if (!latitude || !longitude || !Number.isFinite(Number(latitude)) || !Number.isFinite(Number(longitude))) return null;
  const lat = Number(latitude), lon = Number(longitude);
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { latitude, longitude };
}
