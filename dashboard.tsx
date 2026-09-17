import React, { useMemo } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useAuth } from '@/src/context/AuthContext';
import { useInspections } from '@/src/context/InspectionContext';
import { getVisibleInspections, computeKpis } from '@/src/utils/dataFiltering';
import { hasPermission, roleLabel } from '@/src/utils/permissions';
import { Card, KpiCard, StatusBadge, SectionTitle } from '@/src/components/ui';
import { COLORS, RADIUS, SPACING } from '@/src/theme';

const dashboardTitle = (role: string) => {
  if (role === 'CONTROLLER') return 'State-Level Compliance Dashboard';
  if (role === 'JOINT_CONTROLLER' || role === 'DEPUTY_CONTROLLER') return 'Zone Compliance Dashboard';
  if (role === 'ASSISTANT_CONTROLLER') return 'District Compliance Dashboard';
  if (role === 'SENIOR_INSPECTOR') return 'Field Operations Dashboard';
  return 'My Inspection Dashboard';
};

export default function Dashboard() {
  const { user } = useAuth();
  const { inspections } = useInspections();
  const router = useRouter();

  const visible = useMemo(() => getVisibleInspections(user, inspections), [user, inspections]);
  const kpis = useMemo(() => computeKpis(visible), [visible]);

  const districtStats = useMemo(() => {
    const map = new Map<string, { total: number; nc: number }>();
    visible.forEach(i => {
      const s = map.get(i.district) || { total: 0, nc: 0 };
      s.total += 1;
      if (i.overall_status === 'NON_COMPLIANT') s.nc += 1;
      map.set(i.district, s);
    });
    return Array.from(map.entries()).sort((a, b) => b[1].total - a[1].total).slice(0, 5);
  }, [visible]);

  const recent = visible.slice(0, 5);
  const violations = visible.filter(i => i.overall_status === 'NON_COMPLIANT').slice(0, 5);

  const canScan = hasPermission(user, 'SCAN_IMAGE');

  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={styles.container}>
      <View style={styles.hero}>
        <Text style={styles.heroTitle}>{dashboardTitle(user!.role)}</Text>
        <Text style={styles.heroSub}>{user!.name} · {roleLabel(user!.role)} · {user!.district}</Text>
      </View>

      <View style={styles.kpiGrid}>
        <KpiCard testID="kpi-total" label="Total Inspections" value={kpis.total} icon="clipboard" tone="brand" />
        <KpiCard testID="kpi-compliant" label="Compliant" value={kpis.compliant} icon="check-circle" tone="success" />
        <KpiCard testID="kpi-noncompliant" label="Non-Compliant" value={kpis.nonCompliant} icon="alert-triangle" tone="error" />
        <KpiCard testID="kpi-rate" label="Compliance Rate" value={`${kpis.rate}%`} icon="trending-up" tone="brand" />
      </View>

      {canScan && (
        <View style={styles.quickActions}>
          <Pressable testID="quick-scan-image" style={[styles.action, { backgroundColor: COLORS.brand }]} onPress={() => router.push('/scan-image')}>
            <Feather name="camera" size={20} color="#fff" />
            <Text style={styles.actionText}>Scan Image</Text>
          </Pressable>
          <Pressable testID="quick-scan-url" style={[styles.action, { backgroundColor: COLORS.slate800 }]} onPress={() => router.push('/scan-url')}>
            <Feather name="link" size={20} color="#fff" />
            <Text style={styles.actionText}>Scan URL</Text>
          </Pressable>
        </View>
      )}

      <Card>
        <SectionTitle>Compliance Split</SectionTitle>
        <View style={styles.barContainer}>
          <View style={styles.barTrack}>
            <View style={{ flex: kpis.compliant || 0.001, backgroundColor: COLORS.success }} />
            <View style={{ flex: kpis.nonCompliant || 0.001, backgroundColor: COLORS.error }} />
          </View>
          <View style={styles.legendRow}>
            <View style={styles.legendItem}><View style={[styles.dot, { backgroundColor: COLORS.success }]} /><Text style={styles.legendText}>Compliant · {kpis.compliant}</Text></View>
            <View style={styles.legendItem}><View style={[styles.dot, { backgroundColor: COLORS.error }]} /><Text style={styles.legendText}>Non-Compliant · {kpis.nonCompliant}</Text></View>
          </View>
        </View>
      </Card>

      {districtStats.length > 0 && (
        <Card>
          <SectionTitle>District Performance</SectionTitle>
          {districtStats.map(([d, s]) => {
            const rate = s.total === 0 ? 100 : Math.round(((s.total - s.nc) / s.total) * 100);
            return (
              <View key={d} style={styles.districtRow}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.districtName}>{d}</Text>
                  <View style={styles.districtBarTrack}>
                    <View style={[styles.districtBarFill, { width: `${rate}%`, backgroundColor: rate >= 70 ? COLORS.success : COLORS.warning }]} />
                  </View>
                </View>
                <Text style={styles.districtStat}>{s.total} · {rate}%</Text>
              </View>
            );
          })}
        </Card>
      )}

      <Card>
        <View style={styles.rowSpread}>
          <SectionTitle style={{ marginBottom: 0 }}>Recent Violations</SectionTitle>
          <Pressable onPress={() => router.push('/history')}>
            <Text style={styles.linkText}>View all</Text>
          </Pressable>
        </View>
        {violations.length === 0 ? (
          <Text style={styles.empty}>No violations in your scope. 🎉</Text>
        ) : violations.map(v => (
          <Pressable key={v.id} testID={`violation-${v.id}`} onPress={() => router.push(`/inspection/${v.id}` as any)} style={styles.violationRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.violProduct}>{v.productName}</Text>
              <Text style={styles.violMeta}>{v.location} · {v.inspectorName} · {new Date(v.timestamp).toLocaleDateString()}</Text>
            </View>
            <View style={styles.violCount}>
              <Feather name="alert-triangle" size={12} color={COLORS.error} />
              <Text style={styles.violCountText}>{v.violation_count}</Text>
            </View>
          </Pressable>
        ))}
      </Card>

      <Card>
        <SectionTitle>Recent Inspections</SectionTitle>
        {recent.length === 0 ? (
          <Text style={styles.empty}>No inspections yet. Start by scanning a product.</Text>
        ) : recent.map(i => (
          <Pressable key={i.id} testID={`recent-${i.id}`} onPress={() => router.push(`/inspection/${i.id}` as any)} style={styles.recentRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.recentProduct}>{i.productName}</Text>
              <Text style={styles.recentMeta}>{i.id} · {i.scanType} · {i.location}</Text>
            </View>
            <StatusBadge status={i.overall_status} />
          </Pressable>
        ))}
      </Card>

      <View style={{ height: SPACING.xl }} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: SPACING.lg, gap: SPACING.md },
  hero: { paddingBottom: SPACING.sm },
  heroTitle: { fontSize: 20, fontWeight: '800', color: COLORS.slate900 },
  heroSub: { fontSize: 12, color: COLORS.slate500, marginTop: 4 },
  kpiGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACING.sm },
  quickActions: { flexDirection: 'row', gap: SPACING.sm },
  action: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14, borderRadius: RADIUS.md },
  actionText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  barContainer: { gap: SPACING.md },
  barTrack: { flexDirection: 'row', height: 16, borderRadius: 8, overflow: 'hidden', backgroundColor: COLORS.slate200 },
  legendRow: { flexDirection: 'row', gap: SPACING.lg, flexWrap: 'wrap' },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  legendText: { fontSize: 12, color: COLORS.slate600, fontWeight: '600' },
  districtRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10 },
  districtName: { fontSize: 13, fontWeight: '600', color: COLORS.slate800, marginBottom: 6 },
  districtBarTrack: { height: 6, borderRadius: 3, backgroundColor: COLORS.slate100, overflow: 'hidden' },
  districtBarFill: { height: '100%', borderRadius: 3 },
  districtStat: { fontSize: 12, color: COLORS.slate600, fontWeight: '700', minWidth: 70, textAlign: 'right' },
  rowSpread: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: SPACING.md },
  linkText: { fontSize: 12, color: COLORS.brand, fontWeight: '700' },
  violationRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, borderTopWidth: 1, borderTopColor: COLORS.divider, gap: 8 },
  violProduct: { fontSize: 13, fontWeight: '700', color: COLORS.slate900 },
  violMeta: { fontSize: 11, color: COLORS.slate500, marginTop: 2 },
  violCount: { flexDirection: 'row', alignItems: 'center', gap: 4, backgroundColor: COLORS.errorLight, paddingHorizontal: 10, paddingVertical: 4, borderRadius: RADIUS.pill },
  violCountText: { color: COLORS.error, fontWeight: '800', fontSize: 12 },
  recentRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, borderTopWidth: 1, borderTopColor: COLORS.divider, gap: 12 },
  recentProduct: { fontSize: 13, fontWeight: '700', color: COLORS.slate900 },
  recentMeta: { fontSize: 11, color: COLORS.slate500, marginTop: 2 },
  empty: { color: COLORS.slate500, fontSize: 13, paddingVertical: SPACING.sm },
});
