"use client";

import { Alert, Button, Card, Empty, Flex, List, Skeleton, Tag, Typography } from "antd";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthError } from "@/components/auth-provider";
import { getCampaigns, type DashboardCampaign } from "@/lib/api";

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<DashboardCampaign[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void getCampaigns()
      .then((response) => {
        if (!cancelled) setCampaigns(response.data);
      })
      .catch((problem) => {
        if (!cancelled) setError(problem);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <AuthError error={error} />;
  if (loading) return <Skeleton active paragraph={{ rows: 6 }} />;

  return (
    <Flex vertical gap="large">
      <Flex justify="space-between" align="center" wrap gap="middle">
        <div>
          <Typography.Title level={2}>Campañas</Typography.Title>
          <Typography.Paragraph type="secondary">
            Audiencias, aprobaciones y entregas de la empresa.
          </Typography.Paragraph>
        </div>
        <Button type="primary" disabled>
          Nueva campaña
        </Button>
      </Flex>
      <Card>
        {campaigns.length ? (
          <List
            dataSource={campaigns}
            renderItem={(campaign) => (
              <List.Item actions={[<Link key="view" href={`/campaigns/${campaign.id}`}>Ver detalle</Link>]}>
                <List.Item.Meta
                  title={campaign.name}
                  description={`${campaign.state_label} · ${campaign.delivery_mode}`}
                />
                <Tag>{campaign.discovery_state_label}</Tag>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="Todavía no hay campañas." />
        )}
      </Card>
      <Alert
        type="info"
        showIcon
        message="La creación y aprobación paso a paso se está incorporando de forma segura."
      />
    </Flex>
  );
}
