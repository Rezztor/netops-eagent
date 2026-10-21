/**
 * Backup and restart, filed under Product beside Updates and the support
 * bundle so that everything about keeping the instance itself running is in
 * one place. The work lives where it did; these only adapt it to a section.
 */

import { useToast } from "@/components";
import { InstanceLifecycle } from "../../AuthoringSections";
import { Backup } from "../../OperationsScreen";

export function BackupSettingsSection() {
  const toast = useToast();
  return <Backup onError={(m) => toast.error(m)} onNotice={(m) => toast.success(m)} />;
}

export function RestartSection() {
  return <InstanceLifecycle />;
}
