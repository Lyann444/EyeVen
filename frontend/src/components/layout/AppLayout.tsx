import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  AppBar, Toolbar, Typography, Button, Container, Box,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import ListAltIcon from '@mui/icons-material/ListAlt';

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/';
    return location.pathname.startsWith(path);
  };

  return (
    <>
      <AppBar position="static" color="primary">
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700 }}>
            EyeVen
          </Typography>
          <Button
            color="inherit"
            startIcon={<CloudUploadIcon />}
            onClick={() => navigate('/')}
            sx={{ mr: 1, textTransform: 'none' }}
            variant={isActive('/') && !location.pathname.startsWith('/cases') && !location.pathname.startsWith('/dashboard') ? 'outlined' : 'text'}
          >
            Upload
          </Button>
          <Button
            color="inherit"
            startIcon={<DashboardIcon />}
            onClick={() => navigate('/dashboard')}
            sx={{ mr: 1, textTransform: 'none' }}
            variant={isActive('/dashboard') ? 'outlined' : 'text'}
          >
            Dashboard
          </Button>
          <Button
            color="inherit"
            startIcon={<ListAltIcon />}
            onClick={() => navigate('/cases')}
            sx={{ textTransform: 'none' }}
            variant={isActive('/cases') ? 'outlined' : 'text'}
          >
            Cases
          </Button>
        </Toolbar>
      </AppBar>
      <Container maxWidth="lg" sx={{ mt: 4, mb: 4 }}>
        <Box>
          <Outlet />
        </Box>
      </Container>
    </>
  );
};

export default AppLayout;
