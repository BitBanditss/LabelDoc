import { Redirect } from 'expo-router';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { useAuth } from '@/src/context/AuthContext';
import { COLORS } from '@/src/theme';

export default function Index() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={COLORS.brand} />
      </View>
    );
  }
  return <Redirect href={user ? '/dashboard' : '/login'} />;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.surface },
});
