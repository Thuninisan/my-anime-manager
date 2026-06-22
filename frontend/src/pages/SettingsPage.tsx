import { useNavigate } from 'react-router-dom';
import SettingsModal from '@/components/SettingsModal';

export default function SettingsPage() {
  const navigate = useNavigate();
  return <SettingsModal onClose={() => navigate('/torrent')} />;
}
