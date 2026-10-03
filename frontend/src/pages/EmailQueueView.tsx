import React from "react";
import { MailQueuePage, MailQueuePageProps } from "./MailQueuePage";

export type EmailQueueViewProps = MailQueuePageProps;

export const EmailQueueView: React.FC<EmailQueueViewProps> = (props) => {
  return <MailQueuePage {...props} />;
};

export default EmailQueueView;
