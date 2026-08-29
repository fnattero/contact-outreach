"use client";

import { Tooltip } from "antd";
import { cloneElement, useId, type ReactElement } from "react";

type DisableableControlProps = {
  disabled?: boolean;
  "aria-describedby"?: string;
};

export type DisabledReasonProps = {
  disabled: boolean;
  reason: string;
  children: ReactElement<DisableableControlProps>;
};

export function DisabledReason({ disabled, reason, children }: DisabledReasonProps) {
  const generatedId = useId().replaceAll(":", "");
  if (!disabled) return children;

  const reasonId = `disabled-reason-${generatedId}`;
  const control = cloneElement(children, { disabled: true, "aria-describedby": reasonId });

  return (
    <span className="disabled-reason">
      <Tooltip title={reason} placement="top">
        <span className="disabled-reason__control">{control}</span>
      </Tooltip>
      <span className="disabled-reason__text" id={reasonId}>{reason}</span>
    </span>
  );
}
